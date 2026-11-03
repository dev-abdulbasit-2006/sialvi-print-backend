"""Tests that the Admin and User apps really are gated by the Owner's license.
Loopback server + reserved doc IPs. Run: python -m unittest discover -s tests -v"""
import os, sys, tempfile, threading, unittest, json
from unittest import mock
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_TMP_APPDATA = tempfile.mkdtemp()
os.environ["PROGRAMDATA"] = _TMP_APPDATA            # keep Admin/User config & logs out of the real machine
os.environ["LOCALAPPDATA"] = _TMP_APPDATA
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from werkzeug.serving import make_server
from licensing.server.app import create_app as create_license_app
from licensing.server.config import Settings
from shared import license_gate
from shared.license_token import public_key_pem
from licensing import embed_config
from tests.local_test_support import LocalLicenseClient


class GateBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.lapp = create_license_app(Settings(data_dir=Path(self.tmp.name) / "srv", rate_limit_requests=10_000))
        self.svc = self.lapp.extensions["license_service"]
        self.svc.create_owner("owner", "correct-horse-battery")
        self.srv = make_server("127.0.0.1", 0, self.lapp, threaded=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        pub = Path(self.tmp.name) / "pub.pem"
        pub.write_text(public_key_pem(self.svc.signing_key))
        from shared import license_embedded                       # the apps read ONLY the embedded settings
        self._emb = (license_embedded.SERVER_URL, license_embedded.PUBLIC_KEY_PEM)
        license_embedded.SERVER_URL = f"http://127.0.0.1:{self.srv.server_port}"
        license_embedded.PUBLIC_KEY_PEM = public_key_pem(self.svc.signing_key)
        self._lc = mock.patch.object(license_gate, "LicenseClient", LocalLicenseClient)   # test-only: allows loopback http
        self._lc.start()
        self._orig_dir = license_gate.license_state_dir
        self.state_dir = Path(self.tmp.name) / "pcstate"
        license_gate.license_state_dir = lambda: self.state_dir
        lic, self.key = self.svc.create_license("owner", "Shop", 3)
        self.lic = lic

    def tearDown(self):
        license_gate.license_state_dir = self._orig_dir
        license_gate._gate = None
        from shared import license_embedded
        self._lc.stop()
        license_embedded.SERVER_URL, license_embedded.PUBLIC_KEY_PEM = self._emb
        self.srv.shutdown(); self.tmp.cleanup()

    def inst_id(self, gate):
        return gate.client.state["installation_id"]

    def blank_embedded(self):
        """Simulate a build with NO licensing embedded, whatever is currently shipped in the tree."""
        from shared import license_embedded
        saved = (license_embedded.SERVER_URL, license_embedded.PUBLIC_KEY_PEM)
        license_embedded.SERVER_URL = license_embedded.PUBLIC_KEY_PEM = ""
        def restore():
            license_embedded.SERVER_URL, license_embedded.PUBLIC_KEY_PEM = saved
        self.addCleanup(restore)


class GateTests(GateBase):
    def test_gate_is_configured_from_embedded_settings_only(self):
        """The apps read the embedded settings (test server patched in); nothing else configures licensing."""
        for k in ("SIALVI_LICENSE_SERVER", "SIALVI_LICENSE_PUBLIC_KEY_FILE"):
            os.environ.pop(k, None)
        from shared import license_embedded
        o = (license_embedded.SERVER_URL, license_embedded.PUBLIC_KEY_PEM)
        license_embedded.SERVER_URL = "http://127.0.0.1:8780"
        license_embedded.PUBLIC_KEY_PEM = public_key_pem(self.svc.signing_key)
        try:
            g = license_gate.LicenseGate("Admin")
            self.assertTrue(g.configured); self.assertEqual(g.state().code, "NOT_ACTIVATED")
        finally:
            license_embedded.SERVER_URL, license_embedded.PUBLIC_KEY_PEM = o

    def test_unconfigured_build_never_authorizes(self):
        for k in ("SIALVI_LICENSE_SERVER", "SIALVI_LICENSE_PUBLIC_KEY_FILE"):
            os.environ.pop(k, None)
        self.blank_embedded()
        g = license_gate.LicenseGate("Admin")
        self.assertFalse(g.configured)
        self.assertEqual(g.state().code, "NOT_CONFIGURED")
        self.assertFalse(g.is_authorized())

    def test_no_environment_override_of_licensing_server(self):
        """Cloud-only: no env var / dev setting can repoint licensing at another (local) server."""
        self.blank_embedded()
        os.environ["SIALVI_LICENSE_SERVER"] = "https://evil.example.com"
        os.environ["SIALVI_LICENSE_PUBLIC_KEY_FILE"] = __file__
        try:
            g = license_gate.LicenseGate("Admin")
            self.assertFalse(g.configured)                                 # env vars are ignored
        finally:
            os.environ.pop("SIALVI_LICENSE_SERVER", None); os.environ.pop("SIALVI_LICENSE_PUBLIC_KEY_FILE", None)
        self.assertFalse(hasattr(license_gate, "_dev_settings"))
        orig = license_gate.FROZEN
        try:
            license_gate.FROZEN = True
            license_gate._gate = None
            self.assertFalse(license_gate.license_ok())                   # no gate in a frozen build = refused
            license_gate.FROZEN = False
            self.assertTrue(license_gate.license_ok())                    # from source with no gate: tests/dev only
        finally:
            license_gate.FROZEN = orig

    def test_activate_then_lock_then_unlock_end_to_end(self):
        g = license_gate.init_gate("User", "1.0")
        self.assertFalse(g.is_authorized())
        self.assertEqual(g.state().code, "NOT_ACTIVATED")
        self.assertTrue(g.activate(self.key, "PC-1").authorized)
        self.assertTrue(license_gate.license_ok())
        iid = self.inst_id(g)
        row = self.svc.get_installation(iid)
        self.assertEqual(row["app_version"], "User 1.0")                    # owner sees which app is on the PC
        self.svc.lock_installation("owner", iid, "test")
        self.assertFalse(g.refresh_now().authorized)
        self.assertFalse(license_gate.license_ok())
        g2 = license_gate.init_gate("User", "1.0")                          # app restarted (even offline): still refused
        self.assertEqual(g2.state().code, "INSTALLATION_LOCKED")
        self.svc.unlock_installation("owner", iid)
        self.assertTrue(g2.refresh_now().authorized)

    def test_admin_and_user_on_same_pc_share_one_slot(self):
        a = license_gate.LicenseGate("Admin"); self.assertTrue(a.activate(self.key).authorized)
        u = license_gate.LicenseGate("User")                                 # same state dir -> same installation
        self.assertTrue(u.refresh_now().authorized)
        self.assertEqual(self.svc.get_license(self.lic["id"])["registered_pcs"], 1)

    def test_wrong_or_missing_key_refused(self):
        g = license_gate.init_gate("Admin")
        st = g.activate("SIALVI-AAAA-BBBB-CCCC-DDDD")
        self.assertEqual((st.authorized, st.code), (False, "INVALID_KEY"))
        self.assertFalse(license_gate.license_ok())


class AdminBackendTests(GateBase):
    def test_admin_api_refused_until_licensed_but_health_open(self):
        from admin.server.app import create_app
        g = license_gate.init_gate("Admin")
        c = create_app().test_client()
        self.assertEqual(c.get("/api/health").status_code, 200)
        r = c.get("/api/printers")
        self.assertEqual((r.status_code, r.json["error"]["code"]), (403, "LICENSE_REQUIRED"))
        g.activate(self.key)
        r = c.get("/api/printers")
        self.assertNotEqual(r.json.get("error", {}).get("code") if isinstance(r.json, dict) else None, "LICENSE_REQUIRED")
        self.svc.lock_installation("owner", self.inst_id(g))
        g.refresh_now()
        self.assertEqual(c.get("/api/printers").json["error"]["code"], "LICENSE_REQUIRED")

    def test_queue_pauses_without_license_and_touches_nothing(self):
        from admin.queue import queue_manager as qm
        original = qm.get_conn
        qm.get_conn = lambda: (_ for _ in ()).throw(AssertionError("queue must not read jobs while unlicensed"))
        license_gate.init_gate("Admin")                                      # not activated
        try:
            qm.QueueManager()._process_next_batch()                          # returns immediately
        finally:
            qm.get_conn = original

    def test_license_blocked_job_is_requeued_without_losing_a_retry(self):
        from admin.queue import queue_manager as qm
        captured = []

        class FakeConn:
            def execute(self, sql, params=()):
                captured.append((" ".join(sql.split()), params))

        class FakeTx:
            def __enter__(self): return FakeConn()
            def __exit__(self, *a): return False
        orig_tx = qm.transaction
        qm.transaction = lambda: FakeTx()
        m = qm.QueueManager(); m._sync_order_status = lambda conn, jid: captured.append(("sync", jid))
        try:
            m._requeue_license_blocked({"id": 7, "station_id": "ST1"}, "LICENSE_REQUIRED: this User PC is not licensed right now.")
        finally:
            qm.transaction = orig_tx
        sql = captured[0][0]
        self.assertIn("status = 'QUEUED'", sql); self.assertIn("MAX(attempts - 1, 0)", sql)
        self.assertNotIn("FAILED", sql); self.assertNotIn("DELETE", sql)
        self.assertGreater(m._station_wait["ST1"], 0)


class UserAgentTests(GateBase):
    def test_user_print_agent_refuses_when_unlicensed(self):
        from http.server import ThreadingHTTPServer
        import requests
        from user.network import print_agent as pa
        from user.config.config import config
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), pa._Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        h = {"X-LAN-Token": config.lan_shared_token or "", "X-Station-ID": config.station_id or "",
             "X-Station-Key": config.station_key or "", "X-Job-ID": "J1", "X-Printer-Name": "P"}
        try:
            license_gate.init_gate("User")
            r = requests.post(f"http://127.0.0.1:{httpd.server_port}/agent/print", data=b"x", headers=h, proxies={})
            self.assertEqual(r.status_code, 503)
            self.assertIn("LICENSE_REQUIRED", r.json()["error"])
        finally:
            httpd.shutdown()


class EmbedAndBuildTests(unittest.TestCase):
    def setUp(self):
        from licensing.server import security
        from shared.license_token import generate_private_key_pem, load_private_key
        self.pem = public_key_pem(load_private_key(generate_private_key_pem()))
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_fails_for_empty_embedded_file(self):
        t = Path(self.tmp.name) / "empty.py"
        t.write_text('SERVER_URL = ""\nPUBLIC_KEY_PEM = ""\n')
        ok, msg = embed_config.check(t)
        self.assertFalse(ok)                      # a build with nothing embedded is refused by build_all.bat

    def test_write_and_check(self):
        t = Path(self.tmp.name) / "e.py"
        embed_config.write_embedded("https://license.example.com/", self.pem, t)
        ok, msg = embed_config.check(t)
        self.assertTrue(ok, msg); self.assertIn("license.example.com", msg)
        self.assertFalse(embed_config.fingerprint(self.pem) == "")

    def test_http_and_bad_key_rejected(self):
        t = Path(self.tmp.name) / "e.py"
        with self.assertRaises(ValueError):
            embed_config.write_embedded("http://license.example.com", self.pem, t)
        with self.assertRaises(ValueError):
            embed_config.write_embedded("https://license.example.com", "not a key", t)

    def test_product_client_is_https_only(self):
        from shared.license_client import LicenseClient
        import shared.license_client as lc
        self.assertFalse(hasattr(lc, "is_loopback_url"))
        for bad in ("http://127.0.0.1:8780", "http://localhost:8780", "http://license.example.com", "http://192.168.1.5:8780"):
            with self.assertRaises(ValueError):
                LicenseClient(bad, self.tmp.name, self.pem)
        LicenseClient("https://license.example.com", self.tmp.name, self.pem)
        with self.assertRaises(TypeError):                                # the old escape hatch is gone
            LicenseClient("https://license.example.com", self.tmp.name, self.pem, allow_insecure_http=True)
        LocalLicenseClient("http://127.0.0.1:8780", self.tmp.name, self.pem)   # test-only subclass accepts loopback

    def test_build_script_and_specs_enforce_licensing(self):
        bat = open(os.path.join(ROOT, "installer", "build_all.bat"), "rb").read().decode()
        self.assertLess(bat.index("embed_config.py --check"), bat.index("PyInstaller admin"))
        self.assertIn("--check --online", bat)
        self.assertNotIn("--allow-http", bat)
        for spec in ("admin/build_admin.spec", "user/build_user.spec"):
            text = open(os.path.join(ROOT, spec), encoding="utf-8").read()
            for mod in ("shared.license_gate", "shared.license_embedded", "cryptography"):
                self.assertIn(mod, text, spec)

    def test_both_entry_points_gate_before_any_work(self):
        admin = open(os.path.join(ROOT, "admin", "run_admin.py"), encoding="utf-8").read()
        user = open(os.path.join(ROOT, "user", "run_user.py"), encoding="utf-8").read()
        self.assertLess(admin.index("ensure_licensed(gate)"), admin.index("init_db()"))
        self.assertLess(admin.index("ensure_licensed(gate)"), admin.index("queue_manager.start()"))
        self.assertLess(user.index("ensure_licensed(gate)"), user.index("print_agent.start()"))


try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class LicenseDialogTests(GateBase):
    def test_dialog_activates_and_blocks_when_unconfigured(self):
        from shared.license_ui import LicenseDialog, ensure_licensed
        app = QApplication.instance() or QApplication([])
        g = license_gate.init_gate("User")
        d = LicenseDialog(g)
        self.assertTrue(d.go.isEnabled()); self.assertIn("not activated", d.info.text())
        d.key.setText("sialvi-aaaa-bbbb-cccc-dddd"); self.assertEqual(d.key.text(), "SIALVI-AAAA-BBBB-CCCC-DDDD")  # auto upper-case
        d.activate(); self.assertIn("not valid", d.status.text())               # server's refusal is shown
        d.key.setText(self.key); d.activate()
        self.assertEqual(d.result(), 1)                                         # accepted
        self.assertTrue(g.is_authorized())
        for k in ("SIALVI_LICENSE_SERVER", "SIALVI_LICENSE_PUBLIC_KEY_FILE"):
            os.environ.pop(k, None)
        self.blank_embedded()
        d2 = LicenseDialog(license_gate.LicenseGate("Admin"))
        self.assertFalse(d2.go.isEnabled()); self.assertIn("without a licensing server", d2.info.text())


if __name__ == "__main__":
    unittest.main()


class ResetLicenseTests(GateBase):
    """Reset License: replace only after the new key is accepted; keep the old license otherwise."""

    def test_valid_new_key_replaces_license_and_frees_old_slot(self):
        lic2, key2 = self.svc.create_license("owner", "Shop2", 1)
        g = license_gate.init_gate("Admin", "1.0")
        self.assertTrue(g.activate(self.key, "PC-1").authorized)
        old_id = self.inst_id(g)
        st = g.replace_license(key2, "PC-1")
        self.assertTrue(st.authorized)
        self.assertNotEqual(self.inst_id(g), old_id)
        self.assertEqual(self.svc.get_license(lic2["id"])["registered_pcs"], 1)
        self.assertEqual(self.svc.get_license(self.lic["id"])["registered_pcs"], 0)

    def test_invalid_key_keeps_previous_license(self):
        g = license_gate.init_gate("Admin", "1.0")
        self.assertTrue(g.activate(self.key, "PC-1").authorized)
        before = dict(g.client.state)
        st = g.replace_license("SIALVI-AAAA-BBBB-CCCC-DDDD", "PC-1")
        self.assertFalse(st.authorized)
        self.assertEqual(st.code, "INVALID_KEY")
        self.assertEqual(g.client.state, before)
        self.assertTrue(g.is_authorized())

    def test_limit_reached_keeps_previous_license(self):
        lic2, key2 = self.svc.create_license("owner", "Full", 1)
        other = license_gate.LicenseGate("User")
        other.client.state_path = Path(self.tmp.name) / "other" / "license_state.json"
        other.client.state_path.parent.mkdir(parents=True)
        other.client.state = {}
        self.assertTrue(other.client.activate(key2, "OTHER-PC").authorized)   # fills the 1-PC license
        g = license_gate.init_gate("Admin", "1.0")
        self.assertTrue(g.activate(self.key, "PC-1").authorized)
        st = g.replace_license(key2, "PC-1")
        self.assertEqual(st.code, "LIMIT_REACHED")
        self.assertTrue(g.is_authorized())
        self.assertEqual(self.svc.get_license(self.lic["id"])["registered_pcs"], 1)

    def test_network_failure_keeps_previous_license(self):
        g = license_gate.init_gate("Admin", "1.0")
        self.assertTrue(g.activate(self.key, "PC-1").authorized)
        before = dict(g.client.state)
        g.client.server = "http://127.0.0.1:9"          # nothing listens here
        st = g.replace_license("SIALVI-AAAA-BBBB-CCCC-DDDD", "PC-1")
        self.assertEqual(st.code, "NETWORK")
        self.assertEqual(g.client.state, before)
        self.assertTrue(g.is_authorized())

    def test_admin_window_exposes_reset_license(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "admin", "ui", "main_window.py"), encoding="utf-8").read()
        self.assertIn("Reset License", src)
        self.assertIn("license_gate", src)
