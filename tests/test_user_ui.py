"""Premium User UI: visual-system checks + preservation of every customer-flow handler (mocked API)."""
import hashlib, os, re, sys, tempfile, unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SIALVI_REDUCE_MOTION"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from user.ui import theme_user  # noqa: E402  (pure python, no Qt needed)

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


class ThemeTests(unittest.TestCase):
    def test_text_pairs_meet_wcag_aa(self):
        bad = [(l, round(theme_user.contrast_ratio(f, b), 2)) for f, b, l in theme_user.TEXT_PAIRS
               if theme_user.contrast_ratio(f, b) < 4.5]
        self.assertEqual(bad, [], f"contrast below 4.5:1: {bad}")

    def test_stylesheet_builds_and_covers_dialog_selectors(self):
        css = theme_user.build_user_stylesheet()
        for sel in ("#PageTitle", "#Hint", "#SectionCard", "#PreviewCanvas", "#StatusBannerError",
                    'QPushButton[class="primary"]', 'QPushButton[class="ghost"]', "QMessageBox", "#ConfirmDialog"):
            self.assertIn(sel, css)

    def test_shared_admin_theme_is_unchanged(self):
        from shared.theme import build_stylesheet
        norm = lambda c: re.sub(r"url\([^)]*\)", "url()", c).replace("\r\n", "\n")  # noqa: E731
        self.assertEqual(hashlib.sha256(norm(build_stylesheet(15, True)).encode()).hexdigest(),
                         "6035df9fcc8147d70f28a76e3c92b3b7b40557ba9ddd7ea572928af7871f970b")
        self.assertEqual(hashlib.sha256(norm(build_stylesheet(14, False)).encode()).hexdigest(),
                         "09e08469d98099fdf981e71f443811916f647fa331696864819350bf485dbe65")

    def test_user_theme_is_dark_and_matches_the_admin_palette(self):
        from shared import theme as admin
        for name in ("BG", "SURFACE", "BORDER", "TEXT", "MUTED"):
            self.assertEqual(getattr(theme_user, name), getattr(admin, name), name)
        self.assertEqual(theme_user.ACCENT, admin.ORANGE)
        self.assertEqual(theme_user.NAVY_900, admin.NAVY_DEEP)
        self.assertLess(theme_user._lum(theme_user.BG), 0.02)          # a dark page...
        self.assertLess(theme_user._lum(theme_user.SURFACE), 0.03)      # ...and dark cards
        self.assertGreater(theme_user._lum(theme_user.TEXT), 0.6)       # ...with light text
        css = theme_user.build_user_stylesheet()
        self.assertNotIn("#F8FAFC", css)                                # no leftover light-theme background
        self.assertNotIn("#FFFFFF;", css.split("QTextBrowser")[0].replace("color: #FFFFFF", ""))

    def test_dropdown_arrow_asset_is_light_for_dark_inputs(self):
        from PIL import Image
        path = os.path.join(ROOT, "assets", "arrow_down_light.png")
        self.assertTrue(os.path.exists(path))
        im = Image.open(path).convert("RGBA")
        solid = [im.getpixel((x, y)) for x in range(im.width) for y in range(im.height) if im.getpixel((x, y))[3] > 100]
        self.assertTrue(solid)
        self.assertTrue(all(p[0] > 200 for p in solid))
        self.assertIn("arrow_down_light.png", theme_user.build_user_stylesheet())

    def test_user_styles_module_uses_user_theme(self):
        from user.ui.styles import STYLESHEET
        self.assertIn(theme_user.PRIMARY, STYLESHEET)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class WindowFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from user.ui.styles import STYLESHEET
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLESHEET)

    def setUp(self):
        from user.ui import main_window as mw
        self.mw = mw
        self.fake = mock.MagicMock()
        self.fake.get_printers.return_value = [{"display_name": "P1", "system_name": "P1"}]
        self.fake.get_profiles.return_value = [{"id": 1, "display_name": "BW A4", "ask_properties": 0}]
        self.fake.create_order.return_value = {"order_id": "abcdef12-0000"}
        self._p = mock.patch.object(mw, "api_client", self.fake)
        self._p.start()
        self._pv = mock.patch.object(mw.PreviewPanel, "show_file", lambda *a, **k: None)
        self._pv.start()
        self.w = mw.MainWindow()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.w.poll_timer.stop()
        self._pv.stop(); self._p.stop()
        self.w.allow_close = True
        self.w.close()

    def _file(self, name):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(b"x")
        return p

    def _add(self, *names):
        with mock.patch.object(self.mw.MainWindow, "_start_upload", lambda s, f, r: None):
            self.w._add_files([self._file(n) for n in names])
        return list(self.w.files)

    def test_starts_on_welcome_then_workspace_and_order_created(self):
        self.assertEqual(self.w.stack.currentIndex(), 0)
        self.assertEqual(self.w.order_id, "abcdef12-0000")
        self.assertIn("abcdef12", self.w.order_label.text())
        self.w._on_start_printing()
        self.assertEqual(self.w.stack.currentIndex(), 1)

    def test_start_retries_order_creation_when_startup_failed(self):
        self.w.order_id = None
        self.w._on_start_printing()
        self.assertEqual(self.w.order_id, "abcdef12-0000")

    def test_original_handlers_still_exist(self):
        for name in ("on_select_files", "_start_upload", "_on_upload_progress", "_on_upload_finished",
                     "_on_upload_failed", "on_remove_file", "on_calculate_price", "_on_price_calculated",
                     "_reset_price", "on_pay", "_on_pay_success", "_on_pay_failed", "_poll_order_status",
                     "on_new_customer", "_show_banner", "_hide_banner", "_open_print_properties",
                     "_load_reference_data", "_profile_needs_properties", "closeEvent"):
            self.assertTrue(hasattr(self.w, name), name)
        for attr in ("file_list", "preview", "printer_combo", "profile_combo", "copies_spin",
                     "page_range_input", "orientation_combo", "price_label", "price_caption", "pay_btn", "banner"):
            self.assertTrue(hasattr(self.w, attr), attr)

    def test_steps_follow_state(self):
        keys = self._add("a.pdf")
        self.assertEqual(self.w.steps.step, 0)            # uploading, not ready
        self.w._on_upload_finished(keys[0], {"file_id": 1, "page_count": 3})
        self.assertEqual(self.w.steps.step, 1)
        self.assertEqual(self.w._row_widget_for(keys[0]).chip.text(), "Ready")
        self.w._on_price_calculated({"total_price": 90.0, "unit_price": 30})
        self.assertEqual(self.w.price_label.text(), "Rs 90.00")
        self.assertTrue(self.w.pay_btn.isEnabled())
        self.assertEqual(self.w.steps.step, 2)

    def test_upload_progress_and_failure_states(self):
        k = self._add("a.pdf")[0]
        self.w._on_upload_progress(k, 50, 100)
        row = self.w._row_widget_for(k)
        self.assertEqual(row.chip.text(), "Uploading")
        self.assertEqual(row.progress.value(), 50)
        self.w._on_upload_failed(k, "disk full")
        self.assertEqual(row.chip.text(), "Failed")
        self.assertEqual(self.w.files[k].status, "FAILED")
        self.assertIn("disk full", self.w.banner.text())

    def test_remove_file_calls_api_and_resets_price(self):
        k = self._add("a.pdf")[0]
        self.w._on_upload_finished(k, {"file_id": 7, "page_count": 1})
        self.w._on_price_calculated({"total_price": 10.0})
        self.w.on_remove_file(k)
        self.fake.remove_file.assert_called_once_with("abcdef12-0000", 7)
        self.assertEqual(self.w.files, {})
        self.assertFalse(self.w.pay_btn.isEnabled())
        self.assertEqual(self.w.price_label.text(), "Rs —")

    def test_drop_filters_unsupported_types(self):
        with mock.patch.object(self.mw.MainWindow, "_start_upload", lambda s, f, r: None):
            self.w._on_files_dropped([self._file("ok.pdf"), self._file("virus.exe")])
        self.assertEqual(len(self.w.files), 1)
        self.assertIn("virus.exe", self.w.banner.text())

    def test_price_failure_resets_and_reenables(self):
        k = self._add("a.pdf")[0]
        self.w._on_upload_finished(k, {"file_id": 1, "page_count": 1})
        self.w._on_price_failed("Invalid page range")
        self.assertTrue(self.w.calc_btn.isEnabled())
        self.assertFalse(self.w.pay_btn.isEnabled())
        self.assertIn("Invalid page range", self.w.banner.text())

    def test_pay_cancel_does_not_create_payment(self):
        k = self._add("a.pdf")[0]
        self.w._on_upload_finished(k, {"file_id": 1, "page_count": 1})
        self.w._on_price_calculated({"total_price": 10.0})
        with mock.patch.object(self.mw.ConfirmDialog, "ask", return_value=False), \
             mock.patch.object(self.mw, "PayWorker") as pw:
            self.w.on_pay()
        pw.assert_not_called()
        self.assertTrue(self.w.pay_btn.isEnabled())

    def test_pay_confirm_starts_worker_and_waiting_card(self):
        k = self._add("a.pdf")[0]
        self.w._on_upload_finished(k, {"file_id": 1, "page_count": 1})
        self.w._on_price_calculated({"total_price": 10.0})
        with mock.patch.object(self.mw.ConfirmDialog, "ask", return_value=True) as ask, \
             mock.patch.object(self.mw, "PayWorker") as pw:
            self.w.on_pay()
        self.assertEqual(ask.call_args.kwargs.get("amount"), "Rs 10.00")
        pw.assert_called_once()
        self.assertFalse(self.w.pay_btn.isEnabled())
        self.w._on_pay_success({})
        self.assertEqual(self.w.order_status, "PAID_PENDING_VERIFICATION")
        self.assertFalse(self.w.status_card.isHidden())
        self.assertEqual(self.w.status_card.property("kind"), "waiting")
        self.assertEqual(self.w.steps.step, 3)
        self.w.poll_timer.stop()

    def test_pay_failure_reenables_pay_and_shows_error(self):
        self.w._on_pay_failed("server busy")
        self.assertTrue(self.w.pay_btn.isEnabled())
        self.assertIn("server busy", self.w.banner.text())

    def test_poll_states_are_truthful(self):
        self.w.order_status = "PAID_PENDING_VERIFICATION"
        self.fake.queue_status.return_value = {"order_status": "WAITING_PAYMENT", "jobs": []}
        self.w._poll_order_status()
        self.assertTrue(self.w.status_card.isHidden())       # nothing claimed yet
        self.fake.queue_status.return_value = {"order_status": "PRINTING", "jobs": []}
        self.w._poll_order_status()
        self.assertEqual(self.w.status_card.property("kind"), "printing")
        self.fake.queue_status.return_value = {"order_status": "FAILED",
                                               "jobs": [{"original_filename": "x.pdf", "status": "PRINT_ERROR"}]}
        self.w._poll_order_status()
        self.assertEqual(self.w.status_card.property("kind"), "failed")
        self.assertIn("x.pdf", self.w.status_card.sub.text())
        self.assertNotEqual(self.w.order_status, "PRINTED")
        self.fake.queue_status.return_value = {"order_status": "PRINTED", "jobs": []}
        self.w._poll_order_status()
        self.assertEqual(self.w.status_card.property("kind"), "success")
        self.assertEqual(self.w.steps.step, 4)

    def test_poll_api_error_shows_banner(self):
        self.fake.queue_status.side_effect = self.mw.ApiClientError("offline")
        self.w._poll_order_status()
        self.assertIn("offline", self.w.banner.text())

    def test_new_customer_cancel_keeps_order(self):
        self._add("a.pdf")
        with mock.patch.object(self.mw.ConfirmDialog, "ask", return_value=False):
            self.w.on_new_customer()
        self.assertEqual(len(self.w.files), 1)
        self.fake.cancel_order.assert_not_called()

    def test_new_customer_resets_and_returns_to_welcome(self):
        self.w._on_start_printing()
        k = self._add("a.pdf")[0]
        self.w._on_upload_finished(k, {"file_id": 1, "page_count": 1})
        self.w.copies_spin.setValue(4)
        self.fake.create_order.return_value = {"order_id": "newone-1111"}
        with mock.patch.object(self.mw.ConfirmDialog, "ask", return_value=True):
            self.w.on_new_customer()
        self.fake.cancel_order.assert_called_once_with("abcdef12-0000")   # unpaid draft: best-effort cancel
        self.assertEqual(self.w.files, {})
        self.assertEqual(self.w.copies_spin.value(), 1)
        self.assertEqual(self.w.order_id, "newone-1111")
        self.assertEqual(self.w.stack.currentIndex(), 0)
        self.assertTrue(self.w.status_card.isHidden())
        self.assertEqual(self.w.steps.step, 0)

    def test_new_customer_never_cancels_paid_or_failed_order(self):
        k = self._add("a.pdf")[0]
        self.w._on_upload_finished(k, {"file_id": 1, "page_count": 1})
        self.w.order_status = "PAID_PENDING_VERIFICATION"
        self.fake.queue_status.return_value = {"order_status": "FAILED", "jobs": []}
        self.w._poll_order_status()                       # FAILED must keep the paid status
        with mock.patch.object(self.mw.ConfirmDialog, "ask", return_value=True):
            self.w.on_new_customer()
        self.fake.cancel_order.assert_not_called()

    def test_page_range_hint_validation(self):
        self.w.page_range_input.setText("1-3,5")
        self.assertTrue(self.w.range_hint.isHidden())
        self.w.page_range_input.setText("abc")
        self.assertFalse(self.w.range_hint.isHidden())
        self.assertTrue(self.w.page_range_input.property("invalid"))

    def test_properties_row_toggle_still_works(self):
        self.w._refresh_properties_row()
        self.assertTrue(self.w._options_form.container.isHidden())
        self.w.profiles = [{"id": 1, "display_name": "Glossy", "ask_properties": 1}]
        self.w.profile_combo.clear(); self.w.profile_combo.addItem("Glossy", 1)
        self.w._refresh_properties_row()
        self.assertFalse(self.w._options_form.container.isHidden())

    def test_copies_stepper_and_orientation_follow_original_widgets(self):
        self.w.copies_spin.setValue(999)
        self.assertEqual(self.w.copies_spin.value(), 999)
        self.w.orientation_seg.buttons[2].click()
        self.assertEqual(self.w.orientation_combo.currentText(), "LANDSCAPE")

    def test_window_can_be_maximized_and_starts_maximized(self):
        from PySide6.QtCore import Qt
        self.assertTrue(self.w.windowFlags() & Qt.WindowMaximizeButtonHint)
        self.assertTrue(self.w.windowFlags() & Qt.WindowMinimizeButtonHint)
        self.assertTrue(self.w.windowFlags() & Qt.WindowCloseButtonHint)
        self.w.showMaximized(); self.app.processEvents()
        self.assertTrue(self.w.windowState() & Qt.WindowMaximized)
        self.w.showNormal(); self.app.processEvents()
        self.assertFalse(self.w.windowState() & Qt.WindowMaximized)
        with open(os.path.join(ROOT, "user", "run_user.py"), encoding="utf-8") as fh:
            self.assertIn("window.showMaximized()", fh.read())

    def test_close_still_requires_exit_password(self):
        self.w.allow_close = False
        with mock.patch("user.ui.exit_dialog.confirm_exit", return_value=False):
            ev = mock.MagicMock()
            self.w.closeEvent(ev)
        ev.ignore.assert_called_once()

    def test_screenshots_render_without_error(self):
        self.w.resize(1366, 768)
        self.w.show(); self.app.processEvents()
        self.assertFalse(self.w.grab().isNull())
        self.w._on_start_printing(); self.app.processEvents()
        self.assertFalse(self.w.grab().isNull())


if __name__ == "__main__":
    unittest.main()
