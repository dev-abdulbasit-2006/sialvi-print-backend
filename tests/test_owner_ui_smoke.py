"""Headless smoke test of the Owner License Manager UI against a real loopback server.
Skipped automatically if PySide6 is not installed. Proves the screens load real data and that
lock / limit-conflict flows call the server correctly. It does not prove how it LOOKS on Windows."""
import os, sys, tempfile, threading, unittest, uuid
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from werkzeug.serving import make_server
from tests.local_test_support import LocalOwnerApi
from licensing.server.app import create_app
from licensing.server.config import Settings


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class OwnerUiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.app = create_app(Settings(data_dir=Path(cls.tmp.name), rate_limit_requests=10_000))
        cls.svc = cls.app.extensions["license_service"]
        cls.svc.create_owner("owner", "correct-horse-battery")
        cls.srv = make_server("127.0.0.1", 0, cls.app, threaded=True)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.tmp.cleanup()

    def test_screens_and_actions(self):
        from owner.api import OwnerApi, ApiError
        from owner.ui import main_window as mw
        from owner.ui.dialogs import KeyDialog
        with self.assertRaises(ApiError):                       # owner password never over plain http to a remote host
            OwnerApi("http://license.example.com")
        api = LocalOwnerApi(self.url); api.login("owner", "correct-horse-battery")
        res = api.create_license(label="Printing Shop A", max_installations=2)
        key = res["serial_key"]
        ids = []
        for i in range(2):
            iid = "ui-pc-" + uuid.uuid4().hex[:8]; ids.append(iid)
            self.svc.activate(key, iid, f"PC-{i}", "1.0", "Windows 11", "203.0.113.1" + str(i))
        win = mw.MainWindow(api)
        self.assertEqual(win.lic_table.rowCount(), 1)
        self.assertEqual(win.lic_table.item(0, 3).text(), "2 / 2 PCs")
        self.assertEqual(win.inst_table.rowCount(), 2)
        self.assertEqual(win.cards["registered_installations"].text(), "2")
        kd = KeyDialog("x", key, "0 / 2 PCs")
        self.assertEqual(kd.key.text(), key)                      # dialog shows the full key once
        kd._copy()
        self.assertEqual(self.qt.clipboard().text(), key)         # copy-key button works
        # lock the first PC through the real UI handler (confirmations auto-accepted)
        win.confirm = lambda *a, **k: True
        win.ask_reason = lambda *a, **k: ("test lock", True)
        win.inst_table.selectRow(0)
        from PySide6.QtCore import Qt
        target = next(r for r in win.inst_rows if r["id"] == win.inst_table.item(0, 0).data(Qt.UserRole))
        win.lock()
        self.assertEqual(next(i for i in api.installations() if i["id"] == target["id"])["display_status"], "Locked")
        # limit conflict is surfaced as ApiError(LIMIT_CONFLICT) carrying the installations to choose from
        lid = res["license"]["id"]
        with self.assertRaises(ApiError) as cm:
            api.set_limit(lid, 1)
        self.assertEqual(cm.exception.code, "LIMIT_CONFLICT")
        self.assertEqual(len(cm.exception.extra["installations"]), 2)
        win.refresh_all(); win.tabs.setCurrentIndex(4)
        self.assertGreater(win.audit_table.rowCount(), 3)
        win.close()


if __name__ == "__main__":
    unittest.main()
