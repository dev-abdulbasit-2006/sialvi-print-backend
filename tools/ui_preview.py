"""Offscreen screenshot/smoke harness for the User UI. Uses a FAKE api (never in the shipped app).

    set QT_QPA_PLATFORM=offscreen
    python tools/ui_preview.py [output_dir]
"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest import mock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer


def build_window():
    from user.ui import main_window as mw
    fake = mock.MagicMock()
    fake.get_printers.return_value = [{"display_name": "EPSON L3150 Series", "system_name": "EPSON L3150"}]
    fake.get_profiles.return_value = [
        {"id": 1, "display_name": "Black & White A4", "ask_properties": 0},
        {"id": 2, "display_name": "Color A4 Glossy", "ask_properties": 1}]
    fake.create_order.return_value = {"order_id": "34c6d5b0-aaaa-bbbb-cccc-000000000000"}
    mw.api_client = fake
    return mw, fake


def main(out):
    from user.ui.styles import STYLESHEET
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    mw, fake = build_window()
    os.makedirs(out, exist_ok=True)
    w = mw.MainWindow()
    w.resize(int(os.environ.get("W", 1366)), int(os.environ.get("H", 768)))
    w.show(); app.processEvents()
    w.grab().save(os.path.join(out, "1_welcome.png"))
    w._on_start_printing(); app.processEvents()
    w.grab().save(os.path.join(out, "2_empty.png"))
    # simulate two uploaded files without touching the network
    with mock.patch.object(mw.MainWindow, "_start_upload", lambda self, f, r: None):
        tmp = tempfile.mkdtemp()
        paths = []
        for n in ("task.pdf", "photo.jpg"):
            pth = os.path.join(tmp, n); open(pth, "wb").write(b"x"); paths.append(pth)
        w._add_files(paths)
    keys = list(w.files)
    w._row_widget_for(keys[0]).set_state("uploading", "62%"); w._row_widget_for(keys[0]).set_progress(62)
    w.files[keys[1]].status = "READY"; w._on_upload_finished(keys[1], {"file_id": 9, "page_count": 2})
    app.processEvents(); w.grab().save(os.path.join(out, "3_files.png"))
    w._on_price_calculated({"total_price": 120.0, "unit_price": 60}); app.processEvents()
    w.grab().save(os.path.join(out, "4_price.png"))
    w._on_pay_success({}); app.processEvents(); w.grab().save(os.path.join(out, "5_waiting.png"))
    fake.queue_status.return_value = {"order_status": "PRINTING", "jobs": []}
    w._poll_order_status(); app.processEvents(); w.grab().save(os.path.join(out, "6_printing.png"))
    fake.queue_status.return_value = {"order_status": "PRINTED", "jobs": []}
    w._poll_order_status(); app.processEvents(); w.grab().save(os.path.join(out, "7_done.png"))
    fake.queue_status.return_value = {"order_status": "FAILED", "jobs": [{"original_filename": "task.pdf", "status": "FAILED"}]}
    w._poll_order_status(); app.processEvents(); w.grab().save(os.path.join(out, "8_failed.png"))
    w._show_banner("Upload failed: sample error", "error"); app.processEvents(); w.grab().save(os.path.join(out, "9_error.png"))
    print("saved to", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
