"""
Kiosk main window - implements the full customer workflow from
requirement 15:

  select files -> select printer -> select printing type -> copies ->
  page range -> orientation -> price shown -> PAY -> slip generated ->
  sent to Admin -> poll status until printed

and the NEW CUSTOMER controlled session reset (requirement 16).

Session isolation (requirement 17): every value here lives in instance
state on this window - nothing is global/shared, so multiple User PCs
running this same code never interfere with each other. station_id
(persisted per-PC in config) is attached to every order server-side.
"""

import logging
import os
import uuid

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QIcon, QColor
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QLineEdit, QListWidget, QListWidgetItem, QFileDialog,
    QMessageBox, QFrame, QFormLayout, QSizePolicy, QGraphicsDropShadowEffect,
    QApplication, QDialog,
)

from user.config.config import config
from user.network.api_client import api_client, ApiClientError
from user.ui.workers import UploadWorker, SetOptionsWorker, PayWorker
from user.ui.preview import PreviewPanel
from user.ui.print_properties import PrintPropertiesDialog
from shared.resources import asset_path

logger = logging.getLogger("user_app.main_window")

ORIENTATIONS = ["AUTO", "PORTRAIT", "LANDSCAPE"]
QUEUE_POLL_INTERVAL_MS = 3000


class FileRowWidget(QWidget):
    """One row in the file list: type badge, name, status/progress, remove button."""

    def __init__(self, display_name: str, on_remove):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)

        ext = os.path.splitext(display_name)[1].lstrip(".").upper()[:4] or "FILE"
        badge = QLabel(ext)
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(46, 34)
        badge.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #0E3A6B,stop:1 #05203B);"
            "color: white; border-radius: 7px; font-weight: 800; font-size: 11px;"
        )

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.name_label = QLabel(display_name)
        self.name_label.setStyleSheet("font-weight: 600;")
        self.name_label.setWordWrap(True)
        self.status_label = QLabel("Queued…")
        self.status_label.setObjectName("Muted")
        text_col.addWidget(self.name_label)
        text_col.addWidget(self.status_label)

        self.remove_btn = QPushButton("✕")
        self.remove_btn.setProperty("class", "ghost")
        self.remove_btn.setFixedSize(34, 34)
        self.remove_btn.setStyleSheet("padding: 0;")
        self.remove_btn.clicked.connect(on_remove)

        layout.addWidget(badge)
        layout.addLayout(text_col, 1)
        layout.addWidget(self.remove_btn)

    def set_status(self, text: str):
        self.status_label.setText(text)


class OrderFile:
    """Tracks one file through its local -> uploading -> uploaded lifecycle."""

    def __init__(self, local_key: str, path: str, sequence_index: int):
        self.local_key = local_key
        self.path = path
        self.sequence_index = sequence_index
        self.file_id = None
        self.page_count = None
        self.status = "PENDING"   # PENDING, UPLOADING, READY, FAILED
        self.error_message = None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SialviPrint")
        self.setWindowIcon(QIcon(asset_path("logo_user_horizontal.png")))
        self.resize(1240, 800)
        self.setMinimumSize(1000, 680)
        # Kiosk window: only Minimize and Close buttons (no Maximize). Close
        # (X, Alt+F4, taskbar > Close) is intercepted in closeEvent and needs
        # the exit password. Set allow_close=True only for Windows log-off/shutdown.
        self.allow_close = False
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
                            | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)

        self.order_id = None
        self.order_status = "CREATED"
        self.files: dict[str, OrderFile] = {}
        self._file_list_items: dict[str, QListWidgetItem] = {}
        self._active_uploads: dict[str, UploadWorker] = {}
        self._price_locked = False

        self.printers = []
        self.profiles = []

        self._build_ui()
        self._load_reference_data()
        self._start_new_order()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_order_status)

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
            return
        from user.ui.exit_dialog import confirm_exit
        if confirm_exit(self):
            logger.info("User app closed with the exit password.")
            self.allow_close = True
            event.accept()
        else:
            event.ignore()  # wrong password / cancelled: keep running

    # ------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------
    def _shadow(self, widget):
        fx = QGraphicsDropShadowEffect(widget)
        fx.setBlurRadius(24)
        fx.setOffset(0, 3)
        fx.setColor(QColor(5, 32, 59, 38))
        widget.setGraphicsEffect(fx)

    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- Branded header: supplied horizontal customer logo ----
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(88)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 7, 24, 7)
        hl.setSpacing(14)
        logo = QLabel()
        logo.setMinimumWidth(300)
        pix = QPixmap(asset_path("logo_user_horizontal.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(310, 78, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        hl.addWidget(logo)
        hl.addStretch()
        step = QLabel("New Order")
        step.setObjectName("HeaderSub")
        hl.addWidget(step)
        outer.addWidget(header)

        body = QWidget()
        body.setObjectName("Content")
        root = QVBoxLayout(body)
        root.setContentsMargins(24, 16, 24, 20)
        root.setSpacing(12)

        self.banner = QLabel("")
        self.banner.setObjectName("StatusBanner")
        self.banner.setVisible(False)
        self.banner.setWordWrap(True)
        root.addWidget(self.banner)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        root.addLayout(columns, 1)

        # =========== LEFT: files + options ===========
        left = QVBoxLayout()
        left.setSpacing(14)
        columns.addLayout(left, 5)

        files_card = QFrame()
        files_card.setObjectName("SectionCard")
        self._shadow(files_card)
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(18, 14, 18, 16)
        files_layout.setSpacing(10)

        files_header = QHBoxLayout()
        ft = QLabel("1 · Your Files")
        ft.setObjectName("CardTitle")
        files_header.addWidget(ft)
        files_header.addStretch()
        select_btn = QPushButton("＋  Add Files…")
        select_btn.setProperty("class", "primary")
        select_btn.setCursor(Qt.PointingHandCursor)
        select_btn.clicked.connect(self.on_select_files)
        files_header.addWidget(select_btn)
        files_layout.addLayout(files_header)

        hint = QLabel("PDF, images, Word, PowerPoint, Photoshop, Illustrator, AutoCAD and more. Click a file to preview it.")
        hint.setObjectName("Hint")
        files_layout.addWidget(hint)

        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(150)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        files_layout.addWidget(self.file_list, 1)
        left.addWidget(files_card, 3)

        options_card = QFrame()
        options_card.setObjectName("SectionCard")
        self._shadow(options_card)
        ov = QVBoxLayout(options_card)
        ov.setContentsMargins(18, 14, 18, 16)
        ot = QLabel("2 · Print Options")
        ot.setObjectName("CardTitle")
        ov.addWidget(ot)
        options_layout = QFormLayout()
        options_layout.setHorizontalSpacing(18)
        options_layout.setVerticalSpacing(10)
        ov.addLayout(options_layout)

        self.printer_combo = QComboBox()
        options_layout.addRow("Printer:", self.printer_combo)

        self.profile_combo = QComboBox()
        options_layout.addRow("Printing Type:", self.profile_combo)

        # Shown only for print types that need printer settings (e.g. Glossy)
        self.props_widget = QWidget()
        props_lay = QHBoxLayout(self.props_widget)
        props_lay.setContentsMargins(0, 0, 0, 0)
        props_lay.setSpacing(10)
        self.props_summary = QLabel("Not set")
        self.props_summary.setWordWrap(True)
        self.props_summary.setStyleSheet("font-weight: 600;")
        props_lay.addWidget(self.props_summary, 1)
        self.props_btn = QPushButton("Set Properties…")
        self.props_btn.setProperty("class", "secondary")
        self.props_btn.setCursor(Qt.PointingHandCursor)
        self.props_btn.clicked.connect(self._open_print_properties)
        props_lay.addWidget(self.props_btn)
        options_layout.addRow("Print Properties:", self.props_widget)
        self._options_form = options_layout
        options_layout.setRowVisible(self.props_widget, False)
        self.print_settings = None
        self._native_printer = None
        self.printer_combo.activated.connect(self._on_printer_chosen)
        self.profile_combo.activated.connect(self._on_profile_chosen)

        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, 999)
        self.copies_spin.setValue(1)
        options_layout.addRow("Copies:", self.copies_spin)

        self.page_range_input = QLineEdit()
        self.page_range_input.setPlaceholderText("Leave blank for all pages, e.g. 1-3,5,8-10")
        options_layout.addRow("Page Range:", self.page_range_input)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(ORIENTATIONS)
        options_layout.addRow("Orientation:", self.orientation_combo)
        left.addWidget(options_card, 2)

        new_customer_btn = QPushButton("NEW CUSTOMER")
        new_customer_btn.setProperty("class", "newcustomer")
        new_customer_btn.setCursor(Qt.PointingHandCursor)
        new_customer_btn.clicked.connect(self.on_new_customer)
        nc_row = QHBoxLayout()
        nc_row.addWidget(new_customer_btn)
        nc_row.addStretch()
        left.addLayout(nc_row)

        # =========== RIGHT: preview + price/pay ===========
        right = QVBoxLayout()
        right.setSpacing(14)
        columns.addLayout(right, 4)

        self.preview = PreviewPanel()
        self._shadow(self.preview)
        right.addWidget(self.preview, 1)

        summary = QFrame()
        summary.setObjectName("SectionCard")
        self._shadow(summary)
        sv = QVBoxLayout(summary)
        sv.setContentsMargins(18, 14, 18, 16)
        sv.setSpacing(10)

        st_title = QLabel("3 · Price & Payment")
        st_title.setObjectName("CardTitle")
        sv.addWidget(st_title)

        price_row = QHBoxLayout()
        self.price_caption = QLabel("TOTAL")
        self.price_caption.setObjectName("PriceCaption")
        price_row.addWidget(self.price_caption)
        price_row.addStretch()
        self.price_label = QLabel("Rs —")
        self.price_label.setObjectName("PriceLabel")
        price_row.addWidget(self.price_label)
        sv.addLayout(price_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        calc_btn = QPushButton("Calculate Price")
        calc_btn.setProperty("class", "secondary")
        calc_btn.setCursor(Qt.PointingHandCursor)
        calc_btn.clicked.connect(self.on_calculate_price)
        btn_row.addWidget(calc_btn)

        self.pay_btn = QPushButton("PAY")
        self.pay_btn.setProperty("class", "pay")
        self.pay_btn.setCursor(Qt.PointingHandCursor)
        self.pay_btn.setEnabled(False)
        self.pay_btn.clicked.connect(self.on_pay)
        btn_row.addWidget(self.pay_btn, 1)
        sv.addLayout(btn_row)
        right.addWidget(summary)

        outer.addWidget(body, 1)
        self.setCentralWidget(central)

    def _on_file_selected(self, current, _previous):
        if current is None:
            self.preview.clear()
            return
        key = current.data(Qt.UserRole)
        order_file = self.files.get(key)
        if order_file:
            self.preview.show_file(order_file.path)

    # ------------------------------------------------------------
    # Startup / reference data
    # ------------------------------------------------------------
    def _load_reference_data(self):
        try:
            self.printers = api_client.get_printers() or []
            self.profiles = api_client.get_profiles() or []
            api_client.cache_profiles(self.profiles)
        except ApiClientError as e:
            self._show_banner(e.message, "error")
            return

        self.printer_combo.clear()
        if not self.printers:
            self.printer_combo.addItem("No printers available", None)
        for p in self.printers:
            self.printer_combo.addItem(p["display_name"], p["system_name"])

        self.profile_combo.clear()
        for p in self.profiles:
            self.profile_combo.addItem(p["display_name"], p["id"])
        self.print_settings = None
        if self.profile_combo.count():
            # Apply the mapped default printer without opening a properties dialog
            # during startup.
            self._apply_profile_default_printer(self.profiles[0])
        self._refresh_properties_row()

    def _start_new_order(self):
        try:
            result = api_client.create_order()
        except ApiClientError as e:
            self._show_banner(f"Could not start a new order: {e.message}", "error")
            self.order_id = None
            return
        self.order_id = result["order_id"]
        self.order_status = "CREATED"
        self._hide_banner()

    # ------------------------------------------------------------
    # File selection & upload
    # ------------------------------------------------------------
    def on_select_files(self):
        if not self.order_id:
            self._show_banner("No active order. Please try NEW CUSTOMER.", "error")
            return

        file_filter = (
            "Supported files (*.pdf *.doc *.docx *.xls *.xlsx *.ppt *.pptx *.txt *.rtf *.csv "
            "*.jpg *.jpeg *.png *.bmp *.tiff *.psd *.ai *.dwg *.dxf);;"
            "All files (*)"
        )
        paths, _ = QFileDialog.getOpenFileNames(self, "Select files to print", "", file_filter)
        if not paths:
            return

        start_index = len(self.files)
        for i, path in enumerate(paths):
            local_key = str(uuid.uuid4())
            seq = start_index + i
            order_file = OrderFile(local_key, path, seq)
            self.files[local_key] = order_file

            item = QListWidgetItem()
            item.setData(Qt.UserRole, local_key)
            row_widget = FileRowWidget(
                os.path.basename(path),
                on_remove=lambda _, k=local_key: self.on_remove_file(k),
            )
            item.setSizeHint(row_widget.sizeHint())
            self.file_list.addItem(item)
            self.file_list.setItemWidget(item, row_widget)
            self._file_list_items[local_key] = item

            self._start_upload(order_file, row_widget)

        if self.file_list.currentRow() < 0 and self.file_list.count():
            self.file_list.setCurrentRow(0)   # auto-preview the first file

    def _start_upload(self, order_file: OrderFile, row_widget: FileRowWidget):
        order_file.status = "UPLOADING"
        row_widget.set_status("Uploading… 0%")

        worker = UploadWorker(self.order_id, order_file.path, order_file.sequence_index, order_file.local_key, self)
        worker.progress.connect(lambda key, sent, total: self._on_upload_progress(key, sent, total))
        worker.finished_ok.connect(self._on_upload_finished)
        worker.failed.connect(self._on_upload_failed)
        self._active_uploads[order_file.local_key] = worker
        worker.start()

    def _row_widget_for(self, local_key):
        item = self._file_list_items.get(local_key)
        if not item:
            return None
        return self.file_list.itemWidget(item)

    def _on_upload_progress(self, local_key, sent, total):
        row = self._row_widget_for(local_key)
        if row and total:
            pct = int((sent / total) * 100)
            row.set_status(f"Uploading… {pct}%")

    def _on_upload_finished(self, local_key, result):
        order_file = self.files.get(local_key)
        if not order_file:
            return
        order_file.file_id = result["file_id"]
        order_file.page_count = result["page_count"]
        order_file.status = "READY"

        row = self._row_widget_for(local_key)
        if row:
            row.set_status(f"Ready — {order_file.page_count} page(s)")
        self._active_uploads.pop(local_key, None)

    def _on_upload_failed(self, local_key, message):
        order_file = self.files.get(local_key)
        if order_file:
            order_file.status = "FAILED"
            order_file.error_message = message
        row = self._row_widget_for(local_key)
        if row:
            row.set_status("Failed — tap ✕ to remove")
        self._active_uploads.pop(local_key, None)
        self._show_banner(f"Upload failed: {message}", "error")

    def on_remove_file(self, local_key):
        order_file = self.files.get(local_key)
        if not order_file:
            return

        if order_file.status == "UPLOADING":
            QMessageBox.information(self, "Please Wait", "This file is still uploading.")
            return

        if order_file.file_id and self.order_id:
            try:
                api_client.remove_file(self.order_id, order_file.file_id)
            except ApiClientError as e:
                self._show_banner(e.message, "error")
                return

        item = self._file_list_items.pop(local_key, None)
        if item:
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
        self.files.pop(local_key, None)
        if not self.files:
            self.preview.clear()
        self._reset_price()

    # ------------------------------------------------------------
    # Price calculation
    # ------------------------------------------------------------
    # ------------------------------------------------------------
    # Print properties (paper size / quality / paper type)
    # ------------------------------------------------------------
    def _current_profile(self):
        pid = self.profile_combo.currentData()
        return next((p for p in self.profiles if p["id"] == pid), None)

    def _profile_needs_properties(self) -> bool:
        p = self._current_profile()
        return bool(p and p.get("ask_properties"))

    def _refresh_properties_row(self):
        needs = self._profile_needs_properties()
        self._options_form.setRowVisible(self.props_widget, needs)
        if not needs:
            return
        if self.print_settings:
            s = self.print_settings
            parts = [s.get(k) for k in ("paper_size", "quality", "paper_type") if s.get(k)]
            self.props_summary.setText("  ·  ".join(parts) if parts else "Windows printer settings saved")
            self.props_btn.setText("Change…")
        else:
            self.props_summary.setText("Not set yet")
            self.props_btn.setText("Set Properties…")

    def _apply_profile_default_printer(self, profile=None):
        profile = profile or self._current_profile()
        default_name = (profile or {}).get("default_printer_name") or (profile or {}).get("default_printer_display_name")
        if not default_name:
            return
        for i in range(self.printer_combo.count()):
            name = str(self.printer_combo.itemData(i) or self.printer_combo.itemText(i)).strip()
            if name.lower() == str(default_name).strip().lower():
                self.printer_combo.blockSignals(True)
                self.printer_combo.setCurrentIndex(i)
                self.printer_combo.blockSignals(False)
                return

    def _on_profile_chosen(self, _index):
        self.print_settings = None
        self._apply_profile_default_printer()
        self._reset_price()
        self._refresh_properties_row()
        if self._profile_needs_properties():
            self._open_print_properties()      # pops up straight away

    def _on_printer_chosen(self, _index):
        # Selecting a printer must NOT open the Windows printer-properties dialog.
        # Printer selection only invalidates settings captured for the previous
        # printer. Print properties are opened when the Printing Type is selected
        # (or manually via the Set Properties button).
        self.print_settings = None
        self._native_printer = None
        self._reset_price()
        self._refresh_properties_row()

    def _open_print_properties(self) -> bool:
        """
        Shows the printer's own Windows \"Printing Preferences\" window (paper size,
        quality, paper type ...) when this PC has that printer installed; otherwise
        falls back to the built-in Print Properties popup.
        """
        printer_id = self.printer_combo.currentData()
        profile = self._current_profile()
        if printer_id is None:
            self._show_banner("Please select a printer first.", "error")
            return False
        if profile is None:
            self._show_banner("Please select a printing type first.", "error")
            return False
        result = self._open_native_properties(printer_id)
        if result == "ok":
            self._reset_price()
            self._refresh_properties_row()
            self._hide_banner()
            return True
        if result == "cancel":
            return False
        return self._open_builtin_properties()

    def _open_native_properties(self, printer_id) -> str:
        """Returns 'ok', 'cancel' or 'unavailable' (printer not installed here / not Windows)."""
        from shared import winprint
        if not winprint.IS_WINDOWS:
            return "unavailable"
        local = str(printer_id or "").strip()
        if not local:
            return "unavailable"
        try:
            local_names = winprint.list_local_printers()
            if local not in local_names:
                local = next((n for n in local_names if n.lower() == local.lower()), None)
            if not local:
                logger.info("Printer is no longer installed on this User PC; using the built-in properties popup.")
                return "unavailable"
            initial = None
            if self.print_settings and self.print_settings.get("devmode_b64") and self._native_printer == local:
                initial = winprint.decode(self.print_settings["devmode_b64"])
            raw = winprint.show_printer_preferences(int(self.winId()), local, initial)
        except winprint.WinPrintError as e:
            logger.warning("Native printer preferences unavailable: %s", e)
            return "unavailable"
        if raw is None:
            return "cancel"
        if len(raw) > winprint.MAX_DEVMODE_BYTES:
            return "unavailable"
        info = winprint.describe_for_printer(local, raw)
        self.print_settings = {**info, "devmode_b64": winprint.encode(raw)}
        self._native_printer = local
        return "ok"

    def _open_builtin_properties(self) -> bool:
        printer_id = self.printer_combo.currentData()
        profile = self._current_profile()
        if printer_id is None:
            self._show_banner("Please select a printer first.", "error")
            return False
        if profile is None:
            self._show_banner("Please select a printing type first.", "error")
            return False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            options = api_client.get_print_options(printer_id, profile["id"])
        except ApiClientError as e:
            self._show_banner(e.message, "error")
            return False
        finally:
            QApplication.restoreOverrideCursor()

        dlg = PrintPropertiesDialog(
            self, options.get("printer_name") or self.printer_combo.currentText(),
            profile["display_name"], options, self.print_settings,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        self.print_settings = dlg.values()
        self._reset_price()          # properties changed -> price must be recalculated
        self._refresh_properties_row()
        self._hide_banner()
        return True

    def on_calculate_price(self):
        if not self.order_id:
            self._show_banner("No active order.", "error")
            return

        ready_files = [f for f in self.files.values() if f.status == "READY"]
        if not ready_files:
            self._show_banner("Please add at least one file that has finished uploading.", "error")
            return

        printer_id = self.printer_combo.currentData()
        profile_id = self.profile_combo.currentData()
        if printer_id is None:
            self._show_banner("Please select a printer.", "error")
            return
        if profile_id is None:
            self._show_banner("Please select a printing type.", "error")
            return

        # High-profile print types: properties must be set BEFORE calculating / paying
        if self._profile_needs_properties() and not self.print_settings:
            if not self._open_print_properties():
                self._show_banner("Please set the print properties to continue.", "error")
                return

        copies = self.copies_spin.value()
        orientation = self.orientation_combo.currentText()
        page_range = self.page_range_input.text().strip()

        self.worker_options = SetOptionsWorker(
            self.order_id, printer_id, profile_id, copies, orientation, page_range, self,
            print_settings=self.print_settings if self._profile_needs_properties() else None,
        )
        self.worker_options.finished_ok.connect(self._on_price_calculated)
        self.worker_options.failed.connect(lambda msg: self._show_banner(msg, "error"))
        self.worker_options.start()

    def _on_price_calculated(self, result):
        self._hide_banner()
        self.price_label.setText(f"Rs {result['total_price']:.2f}")
        unit = result.get("unit_price")
        self.price_caption.setText(f"TOTAL  ·  Rs {unit:g} per page" if unit is not None else "TOTAL")
        self._price_locked = True
        self.pay_btn.setEnabled(True)

    def _reset_price(self):
        self.price_caption.setText("TOTAL")
        self.price_label.setText("Rs —")
        self._price_locked = False
        self.pay_btn.setEnabled(False)

    # ------------------------------------------------------------
    # Payment
    # ------------------------------------------------------------
    def on_pay(self):
        if not self._price_locked or not self.order_id:
            return

        reply = QMessageBox.question(
            self, "Confirm Payment",
            f"Total: {self.price_label.text()}\n\nProceed with PAY?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.pay_btn.setEnabled(False)
        self.pay_worker = PayWorker(self.order_id, self)
        self.pay_worker.finished_ok.connect(self._on_pay_success)
        self.pay_worker.failed.connect(self._on_pay_failed)
        self.pay_worker.start()

    def _on_pay_success(self, result):
        self.order_status = "PAID_PENDING_VERIFICATION"
        self._show_banner(
            "Payment slip generated. Please show this screen to the shop attendant "
            "to verify payment. Waiting for verification…", "info",
        )
        self.poll_timer.start(QUEUE_POLL_INTERVAL_MS)

    def _on_pay_failed(self, message):
        self.pay_btn.setEnabled(True)
        self._show_banner(f"Payment could not be submitted: {message}", "error")

    def _poll_order_status(self):
        if not self.order_id:
            self.poll_timer.stop()
            return
        try:
            status = api_client.queue_status(self.order_id)
        except ApiClientError as e:
            self._show_banner(e.message, "error")
            return

        order_status = status["order_status"]
        if order_status == "PRINTED":
            self.poll_timer.stop()
            self._show_banner("✅ Your order has been printed. Thank you!", "success")
        elif order_status == "FAILED":
            self.poll_timer.stop()
            failed_files = [j["original_filename"] for j in status["jobs"] if j["status"] in ("FAILED", "PRINT_ERROR")]
            self._show_banner(
                f"⚠ Printing failed for: {', '.join(failed_files) or 'one or more files'}. "
                f"Please see the shop attendant.", "error",
            )
        elif order_status in ("QUEUED", "PRINTING"):
            self._show_banner("Payment verified. Your order is printing…", "info")
        # else: still WAITING_PAYMENT / PENDING - keep the "waiting for verification" banner as-is

    # ------------------------------------------------------------
    # NEW CUSTOMER — controlled session reset (requirement 16)
    # ------------------------------------------------------------
    def on_new_customer(self):
        if self.files or self._price_locked:
            reply = QMessageBox.question(
                self, "New Customer",
                "This will clear the current order for the next customer. Continue?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.poll_timer.stop()

        # Only cancel server-side if this order was never paid - an
        # already-paid/printing order must never be touched by this
        # reset (requirement 16 explicitly forbids it). The server
        # itself also refuses this, but we avoid even attempting it.
        if self.order_id and self.order_status not in (
            "PAID_PENDING_VERIFICATION", "PAID", "QUEUED", "PRINTING", "PRINTED"
        ):
            try:
                api_client.cancel_order(self.order_id)
            except ApiClientError:
                pass  # best-effort; a stale draft order left behind is harmless

        # Reset ALL local UI/session state
        self.files.clear()
        self._file_list_items.clear()
        self._active_uploads.clear()
        self.file_list.clear()
        self.preview.clear()
        self.copies_spin.setValue(1)
        self.page_range_input.clear()
        self.orientation_combo.setCurrentIndex(0)
        self.print_settings = None
        self._reset_price()
        self._hide_banner()

        self._load_reference_data()   # printers/prices may have changed
        self._start_new_order()

    # ------------------------------------------------------------
    # Banner helper
    # ------------------------------------------------------------
    def _show_banner(self, message: str, kind: str = "info"):
        object_names = {
            "info": "StatusBanner",
            "error": "StatusBannerError",
            "success": "StatusBannerSuccess",
        }
        self.banner.setObjectName(object_names.get(kind, "StatusBanner"))
        self.banner.setText(message)
        self.banner.setVisible(True)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)

    def _hide_banner(self):
        self.banner.setVisible(False)
