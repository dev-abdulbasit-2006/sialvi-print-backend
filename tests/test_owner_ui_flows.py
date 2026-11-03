"""Regression tests for the two Owner License Manager bugs:
  1. 'Create New License' did nothing (a hidden NameError/AttributeError in the dialog-result check).
  2. Tabs/labels were unreadable (no dark-theme rules for tabs, panes, message boxes).
These tests CLICK THROUGH the real dialogs and MEASURE the rendered colours. They run headless
(QT_QPA_PLATFORM=offscreen) and are skipped if PySide6 is missing. They do not prove how it looks on
every Windows PC, but an invisible-text layout cannot pass them.
"""
import ast, os, re, sys, tempfile, threading, unittest
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QMessageBox
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from werkzeug.serving import make_server
from tests.local_test_support import LocalOwnerApi
from licensing.server.app import create_app
from licensing.server.config import Settings
from owner.ui import style


class StaticChecks(unittest.TestCase):
    def test_palette_contrast_is_readable(self):
        for fg, bg in style.CONTRAST_PAIRS:
            self.assertGreaterEqual(style.contrast_ratio(fg, bg), 4.5, f"{fg} on {bg}")
        for name, colour in style.STATUS_COLORS.items():
            self.assertGreaterEqual(style.contrast_ratio(colour, style.SURFACE), 4.5, name)

    def test_no_instance_Accepted_and_QDialog_is_imported(self):
        """The create-license bug: `dlg.Accepted` does not exist in PySide6 and `QDialog` was not imported."""
        for rel in ("owner/ui/main_window.py", "owner/ui/dialogs.py", "owner/run_owner.py"):
            src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
            for m in re.finditer(r"\b(\w+)\.Accepted\b", src):
                self.assertEqual(m.group(1), "DialogCode", f"{rel}: use QDialog.DialogCode.Accepted, not {m.group(0)}")
            if "QDialog.DialogCode" in src:
                imported = {n.id if False else a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) for a in n.names}
                self.assertIn("QDialog", imported, f"{rel} uses QDialog but does not import it")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class UiFlows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = QApplication.instance() or QApplication([])
        cls.qa.setStyle("Fusion")
        cls.qa.setStyleSheet(style.build_owner_stylesheet())
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
        from owner.api import OwnerApi
        from owner.ui.main_window import MainWindow
        api = LocalOwnerApi(self.url); api.login("owner", "correct-horse-battery")
        w = MainWindow(api); w.show(); self.qa.processEvents()
        self.addCleanup(w.close)
        return w, api

    def click(self, dialog, text):
        next(b for b in dialog.findChildren(QPushButton) if b.text() == text).click()

    # ----- bug 1
    def test_create_license_through_the_real_dialog(self):
        from owner.ui.dialogs import CreateLicenseDialog, KeyDialog
        w, api = self.window()
        seen = {}
        def fill_and_create():
            d = self.qa.activeModalWidget()
            seen["first"] = type(d).__name__
            self.click(d, "Create License")                       # empty name: must NOT close, must explain
            seen["error"] = d.error.text(); seen["still_open"] = d.isVisible()
            d.label.setText("Printing Shop A"); d.limit.setValue(5); d.notes.setPlainText("hello")
            self.click(d, "Create License")
        def read_key():
            d = self.qa.activeModalWidget()
            seen["second"] = type(d).__name__
            seen["key"] = d.key.text()
            d.accept()
        QTimer.singleShot(300, fill_and_create)
        QTimer.singleShot(900, read_key)
        w.create_license()
        self.assertEqual(seen["first"], "CreateLicenseDialog")
        self.assertIn("customer / license name", seen["error"]); self.assertTrue(seen["still_open"])
        self.assertEqual(seen["second"], "KeyDialog")
        self.assertRegex(seen["key"], r"^SIALVI(-[A-Z2-9]{4}){4}$")
        lics = api.licenses()
        mine = [(l["label"], l["max_installations"], l["notes"]) for l in lics if l["label"] == "Printing Shop A"]
        self.assertEqual(mine, [("Printing Shop A", 5, "hello")])
        self.assertEqual(w.lic_table.rowCount(), len(lics))        # list refreshed
        self.assertEqual(self.svc.activate(seen["key"], "ui-test-pc-0001", "PC", "1", "os", "203.0.113.5")["authorized"], True)

    def test_cancelled_dialog_creates_nothing(self):
        w, api = self.window()
        before = len(api.licenses())
        QTimer.singleShot(300, lambda: self.click(self.qa.activeModalWidget(), "Cancel"))
        w.create_license()
        self.assertEqual(len(api.licenses()), before)

    def test_server_error_is_shown_not_swallowed(self):
        w, api = self.window()
        shown = []
        orig = QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda parent, title, text, *a, **k: shown.append((title, text)))
        def bad_limit():
            d = self.qa.activeModalWidget(); d.label.setText("X"); d.limit.setValue(1)
            d.use_exp.setChecked(True); d.exp.setText("not-a-date"); self.click(d, "Create License")
        QTimer.singleShot(300, bad_limit)
        try:
            w.create_license()
        finally:
            QMessageBox.warning = orig
        self.assertTrue(shown and "Expiration" in shown[0][1], shown)

    def test_unexpected_exception_is_reported_not_silent(self):
        w, api = self.window()
        shown = []
        orig = QMessageBox.critical
        QMessageBox.critical = staticmethod(lambda parent, title, text, *a, **k: shown.append(text))
        try:
            self.assertIsNone(w.call(lambda: 1 / 0))
        finally:
            QMessageBox.critical = orig
        self.assertTrue(shown and "ZeroDivisionError" in shown[0])

    def test_limit_conflict_and_restriction_dialogs_use_correct_result_check(self):
        from owner.ui import main_window as mw
        w, api = self.window()
        lic, key = self.svc.create_license("owner", "Conflict Shop", 3)
        for i in range(3):
            self.svc.activate(key, f"conf-pc-{i:04d}", f"PC{i}", "1", "os", "203.0.113.9")
        w.refresh_all()
        w.lic_table.selectRow(next(r for r in range(w.lic_table.rowCount()) if w.lic_table.item(r, 1).text() == "Conflict Shop"))
        # real exec() in PySide6 returns an int - emulate exactly that for both dialogs
        mw.LimitDialog.exec = lambda self: (self.limit.setValue(1), 1)[1]
        def conflict_exec(self):
            self.list.item(0).setCheckState(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Checked)
            self.list.item(1).setCheckState(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Checked)
            return 1
        mw.ConflictDialog.exec = conflict_exec
        try:
            w.edit_limit()
        finally:
            del mw.LimitDialog.exec; del mw.ConflictDialog.exec
        got = self.svc.get_license(lic["id"])
        self.assertEqual((got["max_installations"], got["registered_pcs"]), (1, 1))

    def test_action_buttons_follow_selection(self):
        w, api = self.window()
        self.svc.create_license("owner", "Sel Shop", 1); w.refresh_licenses()
        w.lic_table.clearSelection(); w.lic_table.setCurrentCell(-1, -1); self.qa.processEvents()
        self.assertTrue(all(not b.isEnabled() for b in w.lic_actions))
        w.lic_table.selectRow(0); self.qa.processEvents()
        self.assertTrue(all(b.isEnabled() for b in w.lic_actions))
        self.assertEqual(w.selected_row(w.lic_table, w.lic_rows)["_key"], w.lic_rows[w.lic_table.currentRow()]["_key"])

    def test_empty_state_message_when_no_licenses(self):
        s = Settings(data_dir=Path(self.tmp.name) / "empty", rate_limit_requests=10_000)
        app2 = create_app(s); app2.extensions["license_service"].create_owner("owner", "correct-horse-battery")
        srv2 = make_server("127.0.0.1", 0, app2, threaded=True); threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            from owner.api import OwnerApi
            from owner.ui.main_window import MainWindow
            api = LocalOwnerApi(f"http://127.0.0.1:{srv2.server_port}"); api.login("owner", "correct-horse-battery")
            w = MainWindow(api); w.show(); w.tabs.setCurrentIndex(1); self.qa.processEvents()
            self.assertTrue(w.lic_empty.isVisible()); self.assertIn("Create New License", w.lic_empty.text())
            w.close()
        finally:
            srv2.shutdown()

    # ----- bug 2
    @staticmethod
    def _contrast_of(img):
        from collections import Counter
        px = Counter()
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y); px[(c.red(), c.green(), c.blue())] += 1
        bg = px.most_common(1)[0][0]
        hexc = lambda t: "#%02x%02x%02x" % t
        best = max(style.contrast_ratio(hexc(c), hexc(bg)) for c in px)
        return best

    def test_every_rendered_tab_and_button_is_readable(self):
        w, api = self.window()
        bar = w.tabs.tabBar()
        for i in range(w.tabs.count()):
            w.tabs.setCurrentIndex(i); self.qa.processEvents()
            r = bar.tabRect(i).adjusted(6, 6, -6, -4)
            img = bar.grab().toImage().copy(r)
            self.assertGreaterEqual(self._contrast_of(img), 4.5, f"tab '{w.tabs.tabText(i)}' text is not readable")
        w.tabs.setCurrentIndex(1); self.qa.processEvents()
        for b in w.findChildren(QPushButton):
            if b.isVisible() and b.isEnabled() and b.text():
                img = b.grab().toImage().copy(b.rect().adjusted(8, 6, -8, -6))
                self.assertGreaterEqual(self._contrast_of(img), 4.5, f"button '{b.text()}' is not readable")

    def test_error_hook_logs_and_shows_message(self):
        import owner.run_owner as ro
        shown, orig_hook = [], sys.excepthook
        orig = QMessageBox.critical
        QMessageBox.critical = staticmethod(lambda parent, title, text, *a, **k: shown.append(text))
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                os.environ["LOCALAPPDATA"] = d
                ro.install_error_handling()
                try:
                    raise NameError("name 'QDialog' is not defined")
                except NameError:
                    sys.excepthook(*sys.exc_info())
                self.assertTrue(shown and "NameError" in shown[0])
                import logging; logging.shutdown()
                self.assertIn("NameError", (Path(d) / "SialviOwnerLicenseManager" / "owner.log").read_text(encoding="utf-8"))
        finally:
            QMessageBox.critical = orig; sys.excepthook = orig_hook
            logging.getLogger().handlers.clear()


if __name__ == "__main__":
    unittest.main()
