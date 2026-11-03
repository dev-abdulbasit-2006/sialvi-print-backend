"""Owner License Manager: the 'Remote Commands' tab, clicked through the real window (headless Qt).
Skipped automatically when PySide6 is not installed (e.g. the Linux sandbox) - run it on Windows:
    python -m unittest tests.test_owner_remote_tab -v"""
import os, sys, tempfile, threading, unittest
from pathlib import Path
from unittest import mock
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
from owner.ui import style


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class RemoteCommandsTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = QApplication.instance() or QApplication([])
        cls.qa.setStyle("Fusion"); cls.qa.setStyleSheet(style.build_owner_stylesheet())
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

    def window(self):
        from owner.ui.main_window import MainWindow
        api = LocalOwnerApi(self.url); api.login("owner", "correct-horse-battery")
        w = MainWindow(api); w.show(); self.qa.processEvents()
        self.addCleanup(w.close)
        return w

    def test_tab_exists_and_suspend_goes_through_the_real_window(self):
        from owner.ui.main_window import MainWindow
        lic, _key = self.svc.create_license("owner", "Tab Shop", 3)
        w = self.window()
        self.assertIn("Remote Commands", [w.tabs.tabText(i) for i in range(w.tabs.count())])
        w.cmd_license.setCurrentIndex(w.cmd_license.findData(lic["id"]))
        w.cmd_type.setCurrentIndex(w.cmd_type.findData("SUSPEND_LICENSE"))
        self.assertFalse(w.cmd_pc.isEnabled())                       # a license-wide command has no PC choice
        w.cmd_reason.setText("Invoice payment pending")
        with mock.patch.object(MainWindow, "confirm", return_value=True):
            w.send_command()
        self.assertEqual(self.svc.get_license(lic["id"])["status"], "suspended")
        self.assertEqual(len(self.svc.list_commands(lic["id"])), 1)
        self.assertEqual(w.cmd_table.rowCount(), 1)

    def test_reason_is_required_and_nothing_is_sent_without_it(self):
        lic, _key = self.svc.create_license("owner", "No Reason Shop", 2)
        w = self.window()
        w.cmd_license.setCurrentIndex(w.cmd_license.findData(lic["id"]))
        w.cmd_type.setCurrentIndex(w.cmd_type.findData("SUSPEND_LICENSE"))
        w.cmd_reason.setText("")
        with mock.patch.object(QMessageBox, "information") as info:
            w.send_command()
        self.assertTrue(info.called)
        self.assertEqual(self.svc.list_commands(lic["id"]), [])
        self.assertEqual(self.svc.get_license(lic["id"])["status"], "active")

    def test_device_command_needs_a_pc(self):
        lic, _key = self.svc.create_license("owner", "Device Shop", 2)
        w = self.window()
        w.cmd_license.setCurrentIndex(w.cmd_license.findData(lic["id"]))
        w.cmd_type.setCurrentIndex(w.cmd_type.findData("DISABLE_DEVICE"))
        self.assertTrue(w.cmd_pc.isEnabled())
        w.cmd_reason.setText("Stolen PC")
        with mock.patch.object(QMessageBox, "information") as info:
            w.send_command()                                         # "All PCs" selected -> refused, nothing queued
        self.assertTrue(info.called)
        self.assertEqual(self.svc.list_commands(lic["id"]), [])


if __name__ == "__main__":
    unittest.main()
