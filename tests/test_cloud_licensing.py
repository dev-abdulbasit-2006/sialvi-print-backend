"""Cloud-only licensing (Render): no local licensing server concept anywhere in the shipped apps."""
import base64, importlib, io, os, re, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from licensing import embed_config
from shared import cloud_config, license_embedded
from shared.license_client import LicenseClient, validate_public_key_pem
from shared.license_token import generate_private_key_pem, load_private_key, public_key_pem

CLOUD = "https://sialvi-print-backend.onrender.com"


def ed_pub():
    return public_key_pem(load_private_key(generate_private_key_pem()))


def rsa_pub():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore") as fh:
        return fh.read()


class EmbeddedSettingsTests(unittest.TestCase):
    def test_server_url_is_the_render_cloud_service(self):
        self.assertEqual(cloud_config.CLOUD_LICENSE_URL, CLOUD)
        self.assertEqual(license_embedded.SERVER_URL, CLOUD)
        self.assertEqual(license_embedded.LICENSE_SERVER_URL, CLOUD)       # alias for the longer name

    def test_embedded_key_is_empty_or_a_valid_ed25519_key_never_a_placeholder(self):
        pem = license_embedded.PUBLIC_KEY_PEM
        self.assertNotIn("...", pem)
        self.assertNotIn("Keep your existing", pem)
        if pem:
            validate_public_key_pem(pem)

    def test_apps_fail_closed_until_a_real_key_is_embedded(self):
        from shared import license_gate
        with mock.patch.object(license_embedded, "PUBLIC_KEY_PEM", ""):
            g = license_gate.LicenseGate("Admin")
        self.assertFalse(g.configured)
        self.assertFalse(g.is_authorized())

    def test_rsa_or_garbage_key_is_refused_by_gate_and_client(self):
        from shared import license_gate
        for bad in (rsa_pub(), "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0z...\n-----END PUBLIC KEY-----"):
            with self.assertRaises(ValueError):
                LicenseClient(CLOUD, tempfile.mkdtemp(), bad)
            with mock.patch.object(license_embedded, "PUBLIC_KEY_PEM", bad):
                self.assertFalse(license_gate.LicenseGate("User").configured)


class EmbedConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.t = Path(self.tmp.name) / "e.py"
        self.pem = ed_pub()

    def tearDown(self):
        self.tmp.cleanup()

    def test_accepts_the_cloud_url_and_writes_expected_names(self):
        embed_config.write_embedded(CLOUD, self.pem, self.t)
        ns = {}
        exec(self.t.read_text(encoding="utf-8"), ns)
        self.assertEqual(ns["SERVER_URL"], CLOUD)
        self.assertEqual(ns["PUBLIC_KEY_PEM"].strip(), self.pem.strip())
        self.assertEqual(ns["LICENSE_SERVER_URL"], CLOUD)
        ok, msg = embed_config.check(self.t)
        self.assertTrue(ok, msg)

    def test_rejects_every_local_or_insecure_address(self):
        for bad in ("http://sialvi-print-backend.onrender.com", "https://localhost", "https://127.0.0.1:8780",
                    "https://192.168.1.10", "https://10.0.0.5", "https://[::1]", "https://license.local",
                    "https://myserver", "https://printer.lan", "ftp://x.example.com", ""):
            with self.assertRaises(ValueError, msg=bad):
                embed_config.write_embedded(bad, self.pem, self.t)

    def test_rejects_rsa_and_placeholder_keys(self):
        for bad in (rsa_pub(), "", "not a key", "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A...\n-----END PUBLIC KEY-----"):
            with self.assertRaises(ValueError, msg=bad[:30]):
                embed_config.write_embedded(CLOUD, bad, self.t)

    def test_shipped_file_check_reports_missing_key_not_a_crash(self):
        self.t.write_text(f'SERVER_URL = "{CLOUD}"\nPUBLIC_KEY_PEM = ""\n', encoding="utf-8")
        ok, msg = embed_config.check(self.t)
        self.assertFalse(ok)
        self.assertIn("public key", msg.lower())

    def test_old_local_flags_are_gone(self):
        with self.assertRaises(TypeError):
            embed_config.write_embedded(CLOUD, self.pem, self.t, allow_http=True)
        with self.assertRaises(SystemExit):
            embed_config.main(["--allow-http", "--check"])

    def test_online_check_matches_mismatches_and_unreachable(self):
        embed_config.write_embedded(CLOUD, self.pem, self.t)
        with mock.patch.object(embed_config, "fetch_public_key", return_value=self.pem):
            ok, msg = embed_config.check(self.t, online=True)
        self.assertTrue(ok, msg); self.assertIn("Verified online", msg)
        with mock.patch.object(embed_config, "fetch_public_key", return_value=ed_pub()):
            ok, msg = embed_config.check(self.t, online=True)
        self.assertFalse(ok); self.assertIn("does NOT match", msg)
        with mock.patch.object(embed_config, "fetch_public_key", side_effect=RuntimeError("Could not reach")):
            ok, msg = embed_config.check(self.t, online=True)
        self.assertFalse(ok); self.assertIn("Could not reach", msg)

    def test_fetch_retries_while_the_free_host_wakes_up(self):
        calls = []

        def fake_get(url, timeout):
            calls.append(url)
            if len(calls) <= 2:
                raise TimeoutError("cold start")
            return {"status": "ok"} if url.endswith("/health") else {"public_key": self.pem}

        with mock.patch.object(embed_config, "_get_json", side_effect=fake_get):
            self.assertEqual(embed_config.fetch_public_key(CLOUD, attempts=4, pause=0), self.pem)
        self.assertTrue(all(u.startswith(CLOUD + "/api/v1/") for u in calls))
        with mock.patch.object(embed_config, "_get_json", side_effect=TimeoutError("down")):
            with self.assertRaises(RuntimeError):
                embed_config.fetch_public_key(CLOUD, attempts=2, pause=0)


class ScriptInvocationTests(unittest.TestCase):
    """installer/build_all.bat runs embed_config.py as a SCRIPT from the project root (not as a module)."""

    def run_script(self, *args, cwd=None):
        import subprocess
        return subprocess.run([sys.executable, os.path.join(ROOT, "licensing", "embed_config.py"), *args],
                              cwd=cwd or tempfile.gettempdir(), capture_output=True, text=True, timeout=60)

    def test_check_runs_as_a_script_from_any_folder(self):
        r = self.run_script("--check")
        self.assertNotIn("ModuleNotFoundError", r.stderr)
        self.assertIn(r.returncode, (0, 1))
        if not license_embedded.PUBLIC_KEY_PEM:
            self.assertEqual(r.returncode, 1)                       # fail closed: build must stop
            self.assertIn("LICENSING NOT READY", r.stdout)
            self.assertIn("embed_config.py --fetch", r.stdout)

    def test_help_and_old_flags(self):
        self.assertEqual(self.run_script("--help").returncode, 0)
        self.assertNotEqual(self.run_script("--allow-http", "--check").returncode, 0)


class NoLocalLicensingInProductTests(unittest.TestCase):
    FORBIDDEN = ("allow_insecure_http", "SIALVI_LICENSE_SERVER", "SIALVI_LICENSE_ALLOW_HTTP",
                 "SIALVI_LICENSE_PUBLIC_KEY_FILE", "is_loopback_url", "--allow-http", "--insecure-http", "_dev_settings")

    def product_files(self):
        for top in ("shared", "admin", "user", "owner", "installer"):
            for dp, dn, fn in os.walk(os.path.join(ROOT, top)):
                dn[:] = [d for d in dn if d not in ("__pycache__", "Output")]
                for f in fn:
                    if f.endswith((".py", ".bat", ".spec", ".iss")):
                        yield os.path.relpath(os.path.join(dp, f), ROOT)
        yield "licensing/embed_config.py"
        yield "licensing/client_demo.py"

    def test_no_local_licensing_escape_hatches_in_shipped_code(self):
        hits = [(f, t) for f in self.product_files() for t in self.FORBIDDEN if t in read(f)]
        self.assertEqual(hits, [])

    def test_licensing_modules_never_mention_loopback(self):
        # embed_config.py is excluded on purpose: it names "localhost" only to REJECT it.
        pattern = re.compile(r"127\.0\.0\.1|localhost")
        offenders = [f for f in ("shared/license_client.py", "shared/license_gate.py", "shared/license_embedded.py",
                                 "shared/license_ui.py", "shared/cloud_config.py", "owner/api.py", "owner/run_owner.py",
                                 "licensing/client_demo.py") if pattern.search(read(f))]
        self.assertEqual(offenders, [])

    def test_build_scripts_verify_against_the_cloud(self):
        for f in ("installer/build_all.bat", "installer/build_owner_only.bat"):
            text = read(f)
            self.assertIn("embed_config.py --check --online", text, f)
            self.assertNotIn("allow-http", text, f)

    def test_local_server_launcher_removed(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "run_licensing_server.bat")))


class OwnerCloudOnlyTests(unittest.TestCase):
    def test_owner_api_requires_https(self):
        from owner.api import OwnerApi, ApiError
        for bad in ("http://127.0.0.1:8780", "http://localhost", "http://x.example.com"):
            with self.assertRaises(ApiError):
                OwnerApi(bad)
        OwnerApi(CLOUD)
        with self.assertRaises(TypeError):
            OwnerApi(CLOUD, allow_insecure=True)

    def test_login_dialog_has_no_server_field_or_http_toggle(self):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 not installed")
        from owner.run_owner import LoginDialog
        QApplication.instance() or QApplication([])
        d = LoginDialog()
        self.assertEqual(d.server_url, CLOUD)
        self.assertFalse(hasattr(d, "insecure"))
        self.assertFalse(hasattr(d, "url"))


class CloudBackendEntryTests(unittest.TestCase):
    """Root app.py (what Render runs) must be the REAL licensing server, not the old always-ACTIVE stub."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._env = {k: os.environ.get(k) for k in ("LICENSE_DATA_DIR", "LICENSE_SIGNING_KEY_PEM", "LICENSE_KEY_PEPPER")}
        os.environ["LICENSE_DATA_DIR"] = cls.tmp.name
        os.environ.pop("LICENSE_SIGNING_KEY_PEM", None)
        sys.modules.pop("app", None)
        cls.appmod = importlib.import_module("app")
        cls.client = cls.appmod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("app", None)
        for k, v in cls._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        cls.tmp.cleanup()

    def test_health_and_public_key_routes_exist(self):
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
        pk = self.client.get("/api/v1/public-key").get_json()["public_key"]
        validate_public_key_pem(pk)

    def test_old_stub_route_is_gone(self):
        r = self.client.post("/api/client/checkin", json={"hwid": "x", "license_key": "anything"})
        self.assertEqual(r.status_code, 404)

    def test_random_serial_key_is_refused(self):
        r = self.client.post("/api/v1/activate", json={
            "serial_key": "SIALVI-AAAA-BBBB-CCCC-DDDD", "installation_id": "00000000-0000-4000-8000-000000000000",
            "device_name": "PC", "app_version": "1", "os_info": "Windows"})
        self.assertGreaterEqual(r.status_code, 400)
        self.assertNotIn("token", r.get_json())

    def test_requirements_are_minimal_and_render_blueprint_present(self):
        req = read("requirements.txt").lower()
        for need in ("flask", "cryptography", "gunicorn"):
            self.assertIn(need, req)
        yml = read("render.yaml")
        self.assertIn("gunicorn app:app", yml)
        self.assertIn("/api/v1/health", yml)
        self.assertNotRegex(yml, r"LICENSE_SIGNING_KEY_PEM\s*\n\s*value:")        # secrets are never stored in the file


class OwnerBootstrapTests(unittest.TestCase):
    def boot(self, env):
        tmp = tempfile.mkdtemp()
        keys = ("LICENSE_DATA_DIR", "LICENSE_OWNER_USERNAME", "LICENSE_OWNER_PASSWORD")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys[1:]:
                os.environ.pop(k, None)
            os.environ["LICENSE_DATA_DIR"] = tmp
            os.environ.update(env)
            sys.modules.pop("app", None)
            return importlib.import_module("app"), tmp
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            sys.modules.pop("app", None)

    def test_owner_created_from_environment_and_can_log_in(self):
        mod, _ = self.boot({"LICENSE_OWNER_USERNAME": "boss", "LICENSE_OWNER_PASSWORD": "correct-horse-battery"})
        r = mod.app.test_client().post("/api/owner/login", json={"username": "boss", "password": "correct-horse-battery"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        bad = mod.app.test_client().post("/api/owner/login", json={"username": "boss", "password": "wrong-password-123"})
        self.assertGreaterEqual(bad.status_code, 400)

    def test_second_start_is_idempotent_and_no_variables_means_no_owner(self):
        mod, tmp = self.boot({"LICENSE_OWNER_USERNAME": "boss", "LICENSE_OWNER_PASSWORD": "correct-horse-battery"})
        mod._bootstrap_owner(mod.app)                      # already exists: must not raise
        mod2, _ = self.boot({})
        r = mod2.app.test_client().post("/api/owner/login", json={"username": "boss", "password": "correct-horse-battery"})
        self.assertGreaterEqual(r.status_code, 400)

    def test_too_short_password_is_not_accepted(self):
        mod, _ = self.boot({"LICENSE_OWNER_USERNAME": "boss", "LICENSE_OWNER_PASSWORD": "short"})
        r = mod.app.test_client().post("/api/owner/login", json={"username": "boss", "password": "short"})
        self.assertGreaterEqual(r.status_code, 400)


class SecretsFromEnvironmentTests(unittest.TestCase):
    def make(self, **kw):
        from licensing.server.config import Settings
        from licensing.server.service import LicenseService
        d = Path(tempfile.mkdtemp())
        return d, LicenseService(Settings(data_dir=d, **kw))

    def test_signing_key_from_env_survives_a_blank_disk(self):
        pem = generate_private_key_pem()
        expect = public_key_pem(load_private_key(pem))
        for form in (base64.b64encode(pem).decode(), pem.decode(), pem.decode().replace("\n", "\\n")):
            d, svc = self.make(signing_key_pem=form, key_pepper="pepper-1")
            self.assertEqual(public_key_pem(svc.signing_key), expect)
            self.assertFalse((d / "signing_key.pem").exists(), "no key file must be written when the key comes from env")
        # a second 'restarted' instance on a fresh disk has the SAME identity
        _, svc2 = self.make(signing_key_pem=base64.b64encode(pem).decode(), key_pepper="pepper-1")
        self.assertEqual(public_key_pem(svc2.signing_key), expect)

    def test_render_secrets_command_prints_new_values_and_writes_nothing(self):
        from licensing.server.__main__ import main
        target = Path(tempfile.mkdtemp()) / "must-not-exist"
        with mock.patch.dict(os.environ, {"LICENSE_DATA_DIR": str(target)}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["render-secrets"])
        out = buf.getvalue()
        self.assertRegex(out, r"LICENSE_KEY_PEPPER=[0-9a-f]{64}")
        m = re.search(r"LICENSE_SIGNING_KEY_PEM=(\S+)", out)
        self.assertTrue(m)
        validate_public_key_pem(public_key_pem(load_private_key(base64.b64decode(m.group(1)))))
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
