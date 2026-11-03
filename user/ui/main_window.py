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

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QPixmap, QIcon, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QLineEdit, QListWidget, QListWidgetItem, QFileDialog,
    QMessageBox, QFrame, QFormLayout, QSizePolicy, QGraphicsDropShadowEffect,
    QApplication, QDialog, QStackedWidget, QScrollArea, QGridLayout,
)

from user.config.config import config
from user.network.api_client import api_client, ApiClientError
from user.ui.workers import UploadWorker, SetOptionsWorker, PayWorker
from user.ui.preview import PreviewPanel
from user.ui.print_properties import PrintPropertiesDialog
from shared.resources import asset_path
from user.ui import theme_user as T
from user.ui.components import (
    FileRowWidget, StepIndicator, DropZone, StatusCard, SegmentedControl, CopiesStepper,
    WelcomePage, ConfirmDialog, fade_in, switch_page, soft_shadow, repolish, is_supported,
    page_range_looks_valid,
)

logger = logging.getLogger("user_app.main_window")

ORIENTATIONS = ["AUTO", "PORTRAIT", "LANDSCAPE"]
QUEUE_POLL_INTERVAL_MS = 3000


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
        avail = QGuiApplication.primaryScreen().availableGeometry() if QGuiApplication.primaryScreen() else None
        self.resize(min(1240, avail.width()) if avail else 1240, min(800, avail.height()) if avail else 800)
        self.setMinimumSize(1200, 680)   # was 1000: the two-column settings grid needs this width
        # Kiosk window: Minimize, MAXIMIZE and Close buttons (starts maximized, see run_user.py). Close
        # (X, Alt+F4, taskbar > Close) is intercepted in closeEvent and needs
        # the exit password. Set allow_close=True only for Windows log-off/shutdown.
        self.allow_close = False
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
                            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)

        self.order_id = None
        self.order_status = "CREATED"
        self.files: dict[str, OrderFile] = {}
        self._file_list_items: dict[str, QListWidgetItem] = {}
        self._active_uploads: dict[str, UploadWorker] = {}
        self._price_locked = False

        self.printers = []
        self.profiles = []

        self._build_ui()
        self._fade_in()
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
        soft_shadow(widget)

    def _fade_in(self):
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(420)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("Content")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- Header: deep navy band (the logo has white lettering) ----
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(68)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 6, 24, 6)
        hl.setSpacing(14)
        logo = QLabel()
        pix = QPixmap(asset_path("logo_user_horizontal.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(260, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        hl.addWidget(logo)
        hl.addStretch()
        self.order_label = QLabel("")
        self.order_label.setObjectName("HeaderSub")
        hl.addWidget(self.order_label)
        new_customer_btn = QPushButton("NEW CUSTOMER")
        new_customer_btn.setProperty("class", "newcustomer")
        new_customer_btn.setCursor(Qt.PointingHandCursor)
        new_customer_btn.clicked.connect(self.on_new_customer)
        hl.addWidget(new_customer_btn)
        outer.addWidget(header)

        # ---- Status banner (visible on every page: connection / upload / pricing errors) ----
        banner_wrap = QWidget()
        banner_wrap.setObjectName("Content")
        bl = QVBoxLayout(banner_wrap)
        bl.setContentsMargins(24, 0, 24, 0)
        self.banner = QLabel("")
        self.banner.setObjectName("StatusBanner")
        self.banner.setVisible(False)
        self.banner.setWordWrap(True)
        bl.addWidget(self.banner)
        outer.addWidget(banner_wrap)

        # ---- Pages: 0 = welcome, 1 = workspace ----
        self.stack = QStackedWidget()
        self.welcome = WelcomePage()
        self.welcome.start_clicked.connect(self._on_start_printing)
        self.stack.addWidget(self.welcome)

        workspace = QWidget()
        workspace.setObjectName("Page")
        wl = QVBoxLayout(workspace)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        self.steps = StepIndicator()
        wl.addWidget(self.steps)

        body = QWidget()
        body.setObjectName("Content")
        root = QVBoxLayout(body)
        root.setContentsMargins(24, 16, 24, 20)
        root.setSpacing(12)
        body_scroll = QScrollArea()          # only scrolls on very short screens; PAY is never unreachable
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.NoFrame)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_scroll.setWidget(body)
        wl.addWidget(body_scroll, 1)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        root.addLayout(columns, 1)

        # =========== LEFT (scrollable safety net on small screens): files + options ===========
        left_inner = QWidget()
        left = QVBoxLayout(left_inner)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(14)
        columns.addWidget(left_inner, 5)

        files_card = QFrame()
        files_card.setObjectName("SectionCard")
        self._shadow(files_card)
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(18, 14, 18, 16)
        files_layout.setSpacing(10)
        ft = QLabel("Your documents")
        ft.setObjectName("CardTitle")
        files_layout.addWidget(ft)

        self.dropzone = DropZone()
        self.dropzone.browse_requested.connect(self.on_select_files)
        self.dropzone.files_dropped.connect(self._on_files_dropped)
        files_layout.addWidget(self.dropzone)

        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(90)
        self.file_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        files_layout.addWidget(self.file_list, 1)
        left.addWidget(files_card, 1)

        options_card = QFrame()
        options_card.setObjectName("SectionCard")
        self._shadow(options_card)
        ov = QVBoxLayout(options_card)
        ov.setContentsMargins(18, 12, 18, 12)
        ot = QLabel("Print settings")
        ot.setObjectName("CardTitle")
        ov.addWidget(ot)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        ov.addLayout(grid)

        def cell(text, widget):
            box = QWidget()
            bl_ = QVBoxLayout(box)
            bl_.setContentsMargins(0, 0, 0, 0)
            bl_.setSpacing(4)
            lb = QLabel(text)
            lb.setObjectName("FieldLabel")
            bl_.addWidget(lb)
            bl_.addWidget(widget)
            return box

        self.printer_combo = QComboBox()
        self.profile_combo = QComboBox()
        grid.addWidget(cell("Printer", self.printer_combo), 0, 0)
        grid.addWidget(cell("Printing type", self.profile_combo), 0, 1)

        # Shown only for print types that need printer settings (e.g. Glossy)
        self.props_widget = QWidget()
        props_lay = QHBoxLayout(self.props_widget)
        props_lay.setContentsMargins(0, 0, 0, 0)
        props_lay.setSpacing(10)
        self.props_summary = QLabel("Not set")
        self.props_summary.setWordWrap(True)
        self.props_summary.setStyleSheet("font-weight: 700;")
        props_lay.addWidget(self.props_summary, 1)
        self.props_btn = QPushButton("Set Properties…")
        self.props_btn.setProperty("class", "secondary")
        self.props_btn.setCursor(Qt.PointingHandCursor)
        self.props_btn.clicked.connect(self._open_print_properties)
        props_lay.addWidget(self.props_btn)
        props_cell = cell("Print properties", self.props_widget)
        grid.addWidget(props_cell, 1, 0, 1, 2)
        props_cell.setVisible(False)

        class _RowToggle:                   # keeps the original setRowVisible(widget, bool) call sites working
            def __init__(self, container): self.container = container
            def setRowVisible(self, _w, visible): self.container.setVisible(bool(visible))

        self._options_form = _RowToggle(props_cell)
        self.print_settings = None
        self._native_printer = None
        self.printer_combo.activated.connect(self._on_printer_chosen)
        self.profile_combo.activated.connect(self._on_profile_chosen)

        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, 999)
        self.copies_spin.setValue(1)
        grid.addWidget(cell("Copies", CopiesStepper(self.copies_spin)), 2, 0)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(ORIENTATIONS)
        self.orientation_seg = SegmentedControl(
            self.orientation_combo, {"AUTO": "Auto", "PORTRAIT": "Portrait", "LANDSCAPE": "Landscape"})
        grid.addWidget(cell("Orientation", self.orientation_seg), 2, 1)

        range_col = QWidget()
        rc = QVBoxLayout(range_col)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.setSpacing(4)
        self.page_range_input = QLineEdit()
        self.page_range_input.setPlaceholderText("All pages (or e.g. 1-3,5,8-10)")
        self.range_hint = QLabel("Leave blank to print all pages. Example: 1-3,5,8-10")
        self.range_hint.setObjectName("Hint")
        self.range_hint.setVisible(False)       # shown only when the text does not look like a page range
        self.page_range_input.textChanged.connect(self._on_range_text)
        rc.addWidget(self.page_range_input)
        rc.addWidget(self.range_hint)
        grid.addWidget(cell("Page range", range_col), 3, 0, 1, 2)
        left.addWidget(options_card, 0)

        # =========== RIGHT: preview + price/pay + status ===========
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

        st_title = QLabel("Price & payment")
        st_title.setObjectName("CardTitle")
        sv.addWidget(st_title)

        price_row = QHBoxLayout()
        self.price_caption = QLabel("TOTAL")
        self.price_caption.setObjectName("PriceCaption")
        price_row.addWidget(self.price_caption, 0, Qt.AlignBottom)
        price_row.addStretch()
        self.price_label = QLabel("Rs —")
        self.price_label.setObjectName("PriceLabel")
        price_row.addWidget(self.price_label)
        sv.addLayout(price_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self.calc_btn = QPushButton("Calculate Price")
        self.calc_btn.setProperty("class", "secondary")
        self.calc_btn.setCursor(Qt.PointingHandCursor)
        self.calc_btn.clicked.connect(self.on_calculate_price)
        btn_row.addWidget(self.calc_btn)

        self.pay_btn = QPushButton("PAY")
        self.pay_btn.setProperty("class", "pay")
        self.pay_btn.setCursor(Qt.PointingHandCursor)
        self.pay_btn.setEnabled(False)
        self.pay_btn.clicked.connect(self.on_pay)
        btn_row.addWidget(self.pay_btn, 1)
        sv.addLayout(btn_row)

        self.status_card = StatusCard()
        sv.addWidget(self.status_card)
        right.addWidget(summary)

        self.stack.addWidget(workspace)
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._update_order_label()
        self._update_step()

    # ---- presentation helpers (no business logic) ----
    def _update_order_label(self):
        self.order_label.setText(f"Order #{str(self.order_id)[:8]}" if self.order_id else "")

    def _update_step(self):
        """Derive the guided step from state the window already tracks."""
        if self.order_status in ("PRINTED",):
            step = 4                       # everything complete
        elif self.order_status in ("PAID_PENDING_VERIFICATION", "PAID", "QUEUED", "PRINTING", "FAILED"):
            step = 3
        elif self._price_locked:
            step = 2
        elif any(f.status == "READY" for f in self.files.values()):
            step = 1
        else:
            step = 0
        self.steps.set_step(step)

    def _on_start_printing(self):
        if not self.order_id:
            self._start_new_order()        # retry the existing create-order call if startup failed
        switch_page(self.stack, 1)

    def _on_range_text(self, text):
        ok = page_range_looks_valid(text)
        self.page_range_input.setProperty("invalid", not ok)
        repolish(self.page_range_input)
        self.range_hint.setObjectName("Hint" if ok else "HintError")
        self.range_hint.setText("Leave blank to print all pages. Example: 1-3,5,8-10" if ok
                                else "Use page numbers and ranges like 1-3,5,8-10")
        self.range_hint.setVisible(not ok)
        repolish(self.range_hint)

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
            self._update_order_label()
            return
        self.order_id = result["order_id"]
        self.order_status = "CREATED"
        self._hide_banner()
        self._update_order_label()

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
        self._add_files(paths)

    def _on_files_dropped(self, paths):
        if not self.order_id:
            self._show_banner("No active order. Please try NEW CUSTOMER.", "error")
            return
        good = [p for p in paths if is_supported(p)]
        bad = [os.path.basename(p) for p in paths if not is_supported(p)]
        if bad:
            self._show_banner("This file type is not supported: " + ", ".join(bad[:3])
                              + (" …" if len(bad) > 3 else ""), "error")
        if good:
            self._add_files(good)

    def _add_files(self, paths):
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
            item.setSizeHint(QSize(row_widget.sizeHint().width(), 92))
            self.file_list.addItem(item)
            self.file_list.setItemWidget(item, row_widget)
            self._file_list_items[local_key] = item
            fade_in(row_widget, T.DUR_MED)

            self._start_upload(order_file, row_widget)

        if self.file_list.currentRow() < 0 and self.file_list.count():
            self.file_list.setCurrentRow(0)   # auto-preview the first file
        self.dropzone.set_compact(bool(self.files))
        self._update_step()

    def _start_upload(self, order_file: OrderFile, row_widget: FileRowWidget):
        order_file.status = "UPLOADING"
        row_widget.set_state("uploading", "0%")
        row_widget.set_progress(0)

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
            row.set_state("uploading", f"{pct}%")
            row.set_progress(pct)

    def _on_upload_finished(self, local_key, result):
        order_file = self.files.get(local_key)
        if not order_file:
            return
        order_file.file_id = result["file_id"]
        order_file.page_count = result["page_count"]
        order_file.status = "READY"

        row = self._row_widget_for(local_key)
        if row:
            row.set_state("ready", f"{order_file.page_count} page(s)")
        self._active_uploads.pop(local_key, None)
        self._update_step()

    def _on_upload_failed(self, local_key, message):
        order_file = self.files.get(local_key)
        if order_file:
            order_file.status = "FAILED"
            order_file.error_message = message
        row = self._row_widget_for(local_key)
        if row:
            row.set_state("failed", "Tap ✕ to remove this file")
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
        self.dropzone.set_compact(bool(self.files))
        self._reset_price()
        self._update_step()

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

        self.calc_btn.setEnabled(False)
        self.price_label.setText("Calculating…")
        self.worker_options = SetOptionsWorker(
            self.order_id, printer_id, profile_id, copies, orientation, page_range, self,
            print_settings=self.print_settings if self._profile_needs_properties() else None,
        )
        self.worker_options.finished_ok.connect(self._on_price_calculated)
        self.worker_options.failed.connect(self._on_price_failed)
        self.worker_options.start()

    def _on_price_failed(self, message):
        self.calc_btn.setEnabled(True)
        self._reset_price()
        self._show_banner(message, "error")

    def _on_price_calculated(self, result):
        self._hide_banner()
        self.calc_btn.setEnabled(True)
        self.price_label.setText(f"Rs {result['total_price']:.2f}")
        fade_in(self.price_label, T.DUR_MED)
        unit = result.get("unit_price")
        self.price_caption.setText(f"TOTAL  ·  Rs {unit:g} per page" if unit is not None else "TOTAL")
        self._price_locked = True
        self.pay_btn.setEnabled(True)
        self._update_step()

    def _reset_price(self):
        self.price_caption.setText("TOTAL")
        self.price_label.setText("Rs —")
        self._price_locked = False
        self.pay_btn.setEnabled(False)
        if hasattr(self, "steps"):
            self._update_step()

    # ------------------------------------------------------------
    # Payment
    # ------------------------------------------------------------
    def on_pay(self):
        if not self._price_locked or not self.order_id:
            return

        if not ConfirmDialog.ask(
            self, "Confirm payment",
            "A payment slip will be created. Show it to the shop attendant, who will verify your payment.",
            yes="Yes, pay", no="Cancel", amount=self.price_label.text(), default_yes=True,
        ):
            return

        self.pay_btn.setEnabled(False)
        self.pay_worker = PayWorker(self.order_id, self)
        self.pay_worker.finished_ok.connect(self._on_pay_success)
        self.pay_worker.failed.connect(self._on_pay_failed)
        self.pay_worker.start()

    def _on_pay_success(self, result):
        self.order_status = "PAID_PENDING_VERIFICATION"
        self._hide_banner()
        self.status_card.show_state(
            "waiting", "Payment slip created",
            "Please show this screen to the shop attendant to verify your payment. Waiting for verification…")
        self._update_step()
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
        # FAILED deliberately keeps the previous (paid) status so NEW CUSTOMER never tries to cancel a paid order.
        if order_status in ("PRINTED", "QUEUED", "PRINTING"):
            self.order_status = order_status
        if order_status == "PRINTED":
            self.poll_timer.stop()
            self._hide_banner()
            self.status_card.show_state(
                "success", "Your order has been printed. Thank you!",
                "Please collect your documents. Press NEW CUSTOMER when you are finished.")
        elif order_status == "FAILED":
            self.poll_timer.stop()
            failed_files = [j["original_filename"] for j in status["jobs"] if j["status"] in ("FAILED", "PRINT_ERROR")]
            self._hide_banner()
            self.status_card.show_state(
                "failed", "Printing did not complete",
                f"Printing failed for: {', '.join(failed_files) or 'one or more files'}. "
                f"Please see the shop attendant.")
        elif order_status in ("QUEUED", "PRINTING"):
            self._hide_banner()
            self.status_card.show_state(
                "printing", "Payment verified. Your order is printing…",
                "Your documents will be ready in a moment.")
        # else: still WAITING_PAYMENT / PENDING - keep the "waiting for verification" card as-is
        self._update_step()

    # ------------------------------------------------------------
    # NEW CUSTOMER — controlled session reset (requirement 16)
    # ------------------------------------------------------------
    def on_new_customer(self):
        if self.files or self._price_locked:
            if not ConfirmDialog.ask(
                self, "New customer",
                "This will clear the current order for the next customer. Continue?",
                yes="Yes, clear it", no="Keep order", default_yes=False,
            ):
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
        self.status_card.clear()
        self.dropzone.set_compact(False)
        self.calc_btn.setEnabled(True)
        self._reset_price()
        self._hide_banner()

        self._load_reference_data()   # printers/prices may have changed
        self._start_new_order()
        self._update_step()
        switch_page(self.stack, 0)    # clean welcome screen for the next customer

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
