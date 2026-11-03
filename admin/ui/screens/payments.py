"""Figma-style payment verification screen backed by the real API."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QBrush
try:
    import winsound
except ImportError:  # non-Windows development environments
    winsound = None

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QHeaderView
)
from admin.ui.api_client import api_client, ApiClientError


# Columns (0-based) that keep the orange highlight: 4 = Printer, 6 = Amount
HIGHLIGHT_COLUMNS = (4, 6)


class PaymentsScreen(QWidget):
    def __init__(self):
        super().__init__()
        self._known_payment_ids = set()
        self._payments_initialized = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("Pending Payments")
        title.setObjectName("PageTitle")
        title_col.addWidget(title)
        sub = QLabel("Verify customer payments before releasing print jobs")
        sub.setObjectName("Hint")
        title_col.addWidget(sub)
        head.addLayout(title_col)
        head.addStretch()
        self.count = QLabel("")
        self.count.setStyleSheet("color:#F59E0B;background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.25);border-radius:8px;padding:8px 12px;font-weight:600;")
        head.addWidget(self.count)
        refresh = QPushButton("Refresh")
        refresh.setProperty("class", "primary")
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        lay.addLayout(head)

        # Figma-style filter tabs. The API only returns pending payments, so
        # verified/rejected are represented through receipt history below.
        tabs = QHBoxLayout()
        tabs.setSpacing(3)
        self.tab_buttons = []
        for label, method in (("All", "all"), ("Pending", "pending")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(method == "all")
            b.setProperty("class", "primary" if method == "all" else "ghost")
            b.clicked.connect(lambda _, m=method: self.set_filter(m))
            tabs.addWidget(b)
            self.tab_buttons.append((method, b))
        history = QPushButton("Receipt History")
        history.setProperty("class", "ghost")
        history.clicked.connect(self.open_receipts)
        tabs.addWidget(history)
        tabs.addStretch()
        lay.addLayout(tabs)

        self.error = QLabel("")
        self.error.setObjectName("StatusBannerError")
        self.error.setVisible(False)
        self.error.setWordWrap(True)
        lay.addWidget(self.error)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Order ID", "Computer", "Files", "Printing", "Printer", "Pages", "Amount", "Payment Actions"
        ])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(7, 320)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(62)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table, 1)

        self._filter = "all"
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(4000)
        self.refresh()

    def set_filter(self, value):
        self._filter = value
        for method, button in self.tab_buttons:
            button.setProperty("class", "primary" if method == value else "ghost")
            button.style().unpolish(button)
            button.style().polish(button)
        self.refresh()

    def open_receipts(self):
        from admin.ui.screens.dashboard import ReceiptHistoryDialog
        ReceiptHistoryDialog("today", parent=self).exec()

    def refresh(self):
        try:
            rows = api_client.get_pending_payments()
        except ApiClientError as e:
            self.error.setText("⚠ " + e.message)
            self.error.setVisible(True)
            return
        self.error.setVisible(False)
        self.count.setText(f"{len(rows)} awaiting verification")

        current_ids = {str(p.get("payment_id")) for p in rows if p.get("payment_id")}
        new_ids = current_ids - self._known_payment_ids
        if self._payments_initialized and new_ids:
            self._alert_new_payment()
        self._known_payment_ids = current_ids
        self._payments_initialized = True
        self.table.setRowCount(len(rows))

        for r, p in enumerate(rows):
            vals = [
                str(p.get("order_id", ""))[:8],
                p.get("computer_name") or p.get("station_id", ""),
                p.get("file_names") or "—",
                p.get("printing_type") or "—",
                p.get("printer_name") or "—",
                str(p.get("total_pages", 0)),
                f"PKR {float(p.get('amount', 0)):,.0f}",
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c in HIGHLIGHT_COLUMNS:          # only Printer and Amount are highlighted; the rest use the normal theme colors
                    item.setBackground(QBrush(QColor("#F29931")))
                    item.setForeground(QBrush(QColor("#173241")))
                self.table.setItem(r, c, item)

            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(4, 4, 4, 4)
            al.setSpacing(6)
            actions.setMinimumWidth(308)
            actions.setStyleSheet("background:transparent;")
            cash = QPushButton("✓  Cash")
            cash.setProperty("class", "cash")
            cash.clicked.connect(lambda _, pid=p["payment_id"]: self.confirm(pid, "CASH"))
            online = QPushButton("✓  Online")
            online.setProperty("class", "online")
            online.clicked.connect(lambda _, pid=p["payment_id"]: self.confirm(pid, "ONLINE"))
            reject = QPushButton("✕  Reject")
            reject.setProperty("class", "danger")
            reject.clicked.connect(lambda _, pid=p["payment_id"]: self.reject(pid))
            cash.setFixedWidth(92)
            online.setFixedWidth(102)
            reject.setFixedWidth(92)
            for b in (cash, online, reject):
                al.addWidget(b)
            self.table.setCellWidget(r, 7, actions)

    def _alert_new_payment(self):
        """Use the normal Windows system alert sound when a new payment arrives."""
        if winsound is None:
            return
        try:
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass

    def confirm(self, payment_id, method):
        """Verify the payment immediately (no confirmation / success pop-ups). Errors still show a message."""
        label = "cash" if method == "CASH" else "online"
        try:
            result = api_client.verify_payment(payment_id, method)
        except ApiClientError as e:
            QMessageBox.critical(self, "Payment failed", e.message)
            return
        self.refresh()
        note = (f"✓ {label.title()} payment confirmed · Receipt #{int(result.get('receipt_no', 0) or 0):06d}"
                f" · {result.get('print_jobs_created', 0)} job(s) queued")
        base = self.count.text()
        self.count.setText(f"{base}   |   {note}")
        QTimer.singleShot(6000, lambda: self.count.setText(base) if self.count.text().endswith(note) else None)

    def reject(self, payment_id):
        if QMessageBox.question(
            self, "Reject payment", "Reject this payment request?",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return
        try:
            api_client.reject_payment(payment_id)
        except ApiClientError as e:
            QMessageBox.critical(self, "Reject failed", e.message)
            return
        self.refresh()
