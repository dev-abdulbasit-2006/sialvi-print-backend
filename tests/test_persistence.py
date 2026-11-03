"""Regression for the 'works today, "This installation is not recognized" tomorrow' bug.

Root cause: on a host with an ephemeral disk (Render free tier) the SQLite file is wiped whenever the service
restarts / sleeps, so the server forgot every license and PC. Fix: PostgreSQL via DATABASE_URL.

* Always run: the symptom with a wiped SQLite file, the SQL translator, health reporting, settings.
* Run only with TEST_DATABASE_URL (an empty scratch PostgreSQL database): the same restart on PostgreSQL keeps everything.
"""
import base64, os, sys, tempfile, unittest, uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from licensing.server import db
from licensing.server.config import Settings
from licensing.server.service import LicenseError, LicenseService
from shared.license_token import generate_private_key_pem

PG = os.environ.get("TEST_DATABASE_URL", "").strip()
SIGNING = base64.b64encode(generate_private_key_pem()).decode()      # like LICENSE_SIGNING_KEY_PEM on Render
PEPPER = "pepper-for-persistence-tests"


def make_service(url=""):
    d = Path(tempfile.mkdtemp())              # a brand-new, EMPTY disk: what Render gives after a restart
    return LicenseService(Settings(data_dir=d, key_pepper=PEPPER, signing_key_pem=SIGNING, database_url=url))


def activate(svc, key, inst):
    return svc.activate(key, inst, "PC-1", "1.0", "Windows", "203.0.113.9")


class SymptomOnEphemeralSqliteTests(unittest.TestCase):
    def test_wiped_sqlite_makes_every_pc_unrecognized_and_keys_unknown(self):
        """This IS the reported bug: same secrets, but the disk was wiped."""
        before = make_service()
        _, key = before.create_license("owner", "Shop", 2)
        inst = str(uuid.uuid4())
        got = activate(before, key["key"] if isinstance(key, dict) else key, inst)
        secret = got["installation_secret"]
        before.checkin(inst, secret, "1.0", "203.0.113.9")                 # fine today

        after = make_service()                                              # 'tomorrow': new empty disk
        with self.assertRaises(LicenseError) as cm:
            after.checkin(inst, secret, "1.0", "203.0.113.9")
        self.assertEqual(cm.exception.code, "INVALID_CREDENTIALS")          # "This installation is not recognized."
        with self.assertRaises(LicenseError) as cm2:
            activate(after, key["key"] if isinstance(key, dict) else key, str(uuid.uuid4()))
        self.assertEqual(cm2.exception.code, "INVALID_KEY")                 # the serial key vanished too

    def test_signing_key_from_environment_is_stable_across_wipes(self):
        from licensing.server.security import load_or_create_server_secrets
        a = make_service().signing_key.public_key().public_bytes_raw()
        b = make_service().signing_key.public_key().public_bytes_raw()
        self.assertEqual(a, b)                                              # tokens stay verifiable by installed apps


class TranslatorTests(unittest.TestCase):
    def test_sqlite_sql_becomes_postgres_sql(self):
        self.assertEqual(db.translate("SELECT * FROM t WHERE a=? AND b=?"), "SELECT * FROM t WHERE a=%s AND b=%s")
        self.assertIn("SERIAL PRIMARY KEY", db.translate("id INTEGER PRIMARY KEY AUTOINCREMENT,"))
        self.assertNotIn("AUTOINCREMENT", db.translate("id INTEGER PRIMARY KEY AUTOINCREMENT,"))
        like = db.translate("WHERE label LIKE ? OR ('LIC-' || printf('%05d', id)) LIKE ?")
        self.assertIn("ILIKE", like)
        self.assertIn("lpad(CAST(id AS TEXT), 5, '0')", like)
        self.assertNotIn("printf", like)
        self.assertNotIn("?", like)
        self.assertIn("%%", db.translate("SELECT '100%' FROM t"))           # literal percent signs survive psycopg2

    def test_target_detection(self):
        self.assertTrue(db.is_postgres("postgresql://u:p@h/db"))
        self.assertTrue(db.is_postgres("postgres://u:p@h/db"))
        self.assertFalse(db.is_postgres(Path("x.db")))
        self.assertFalse(db.is_postgres(""))


class SettingsAndHealthTests(unittest.TestCase):
    def test_database_url_is_read_from_environment(self):
        keep = {k: os.environ.pop(k, None) for k in ("DATABASE_URL", "LICENSE_DATABASE_URL")}
        try:
            os.environ["DATABASE_URL"] = "postgresql://u:p@h/db"
            self.assertEqual(Settings.from_env().database_url, "postgresql://u:p@h/db")
            os.environ.pop("DATABASE_URL")
            os.environ["LICENSE_DATABASE_URL"] = "postgresql://x:y@z/db2"
            self.assertEqual(Settings.from_env().database_url, "postgresql://x:y@z/db2")
        finally:
            for k in ("DATABASE_URL", "LICENSE_DATABASE_URL"):
                os.environ.pop(k, None)
            for k, v in keep.items():
                if v is not None:
                    os.environ[k] = v

    def test_health_says_whether_data_survives_a_restart(self):
        from licensing.server.app import create_app
        svc = make_service()
        app = create_app(svc.s)
        body = app.test_client().get("/api/v1/health").get_json()
        self.assertEqual(body["storage"], "sqlite")
        self.assertFalse(body["persistent"])


class DeploymentFilesTests(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_requirements_and_render_blueprint_support_persistent_storage(self):
        self.assertIn("psycopg2-binary", self.read("requirements.txt").lower())
        yml = self.read("render.yaml")
        self.assertIn("DATABASE_URL", yml)
        import re
        self.assertNotRegex(yml, r"DATABASE_URL\s*#?[^\n]*\n\s*value:")   # the connection string is a secret, never in the file
        self.assertIn("app.py", "app.py")

    def test_app_warns_loudly_when_running_on_render_without_a_database(self):
        src = self.read("app.py")
        self.assertIn("DATABASE_URL is NOT set", src)

    def test_owner_api_and_window_know_about_storage(self):
        self.assertIn("def health", self.read("owner/api.py"))
        win = self.read("owner/ui/main_window.py")
        self.assertIn("storage_banner", win)
        self.assertIn("TEMPORARY disk", win)

    def test_customer_message_explains_what_to_do(self):
        from licensing.server.service import CLIENT_MESSAGES as MESSAGES
        msg = MESSAGES["INVALID_CREDENTIALS"]
        self.assertIn("activate it again", msg)
        self.assertIn("serial key", msg)


@unittest.skipUnless(PG, "set TEST_DATABASE_URL to an empty scratch PostgreSQL database")
class PostgresPersistenceTests(unittest.TestCase):
    def setUp(self):
        import psycopg2
        self.schema = "p_" + uuid.uuid4().hex[:10]
        c = psycopg2.connect(PG); c.autocommit = True
        c.cursor().execute(f"CREATE SCHEMA {self.schema}"); c.close()
        self.url = f"{PG}{'&' if '?' in PG else '?'}options=-csearch_path%3D{self.schema}"

    def tearDown(self):
        db.close_pools()
        import psycopg2
        c = psycopg2.connect(PG); c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA {self.schema} CASCADE"); c.close()

    def test_restart_on_a_wiped_disk_keeps_licenses_and_pcs(self):
        """The fix: same restart as the SQLite test, but the data lives in PostgreSQL."""
        before = make_service(self.url)
        before.create_owner("boss", "correct-horse-battery")
        lic, key = before.create_license("owner", "Shop", 2)
        key = key["key"] if isinstance(key, dict) else key
        inst = str(uuid.uuid4())
        secret = activate(before, key, inst)["installation_secret"]

        after = make_service(self.url)                                      # restart: new empty disk, same database
        self.assertEqual(after.checkin(inst, secret, "1.0", "203.0.113.9")["authorized"], True)
        self.assertEqual(after.get_license(lic["id"])["label"], "Shop")
        self.assertEqual(len(after.list_installations(lic["id"])), 1)
        self.assertTrue(after.login("boss", "correct-horse-battery", "198.51.100.1")["token"])
        activate(after, key, str(uuid.uuid4()))                             # 2nd PC still fits the limit of 2
        with self.assertRaises(LicenseError) as cm:
            activate(after, key, str(uuid.uuid4()))                         # the 3rd is refused, as before
        self.assertEqual(cm.exception.code, "LIMIT_REACHED")

    def test_health_reports_postgresql(self):
        from licensing.server.app import create_app
        svc = make_service(self.url)
        body = create_app(svc.s).test_client().get("/api/v1/health").get_json()
        self.assertEqual((body["storage"], body["persistent"]), ("postgresql", True))

    def test_keys_are_stored_hashed_and_search_works(self):
        import psycopg2
        svc = make_service(self.url)
        _, key = svc.create_license("owner", "Alpha Shop", 1)
        key = key["key"] if isinstance(key, dict) else key
        c = psycopg2.connect(self.url); cur = c.cursor()
        cur.execute("SELECT string_agg(key_hash || key_hint || label, ',') FROM licenses")
        raw = cur.fetchone()[0]; c.close()
        self.assertNotIn(key, raw)                                          # plain key never stored
        self.assertTrue(svc.list_licenses(q="alpha"))                       # LIKE became case-insensitive ILIKE
        self.assertTrue(svc.list_licenses(q="LIC-00001"))                   # printf('%05d') translation


if __name__ == "__main__":
    unittest.main()
