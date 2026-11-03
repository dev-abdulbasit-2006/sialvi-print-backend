"""Admin dashboard and receipt history, matching the supplied SialviPrint design."""
import csv
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QFrame,
    QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QLineEdit, QComboBox, QMessageBox, QFileDialog,
)
from admin.ui.api_client import api_client, ApiClientError


def money(v):
    return f"PKR {float(v or 0):,.0f}"


def local_time_text(value) -> str:
    """Show a stored UTC-naive timestamp (e.g. 2026-10-12T06:22:53.1) as local time."""
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        dt = datetime.fromisoformat(text.replace(" ", "T")[:26])
        return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text.replace("T", " ")[:19]


def csv_safe(value) -> str:
    """Neutralise spreadsheet formulas. File/printer/computer names come from
    customers, and Excel runs cells that start with = + - @ as formulas."""
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


PERIOD_LABELS = {"today": "Today", "week": "This Week", "month": "This Month", "all": "All History"}


def pct_text(v):
    v = float(v or 0)
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.0f}% vs previous"


def badge(text: str, status: str) -> QLabel:
    colors = {
        "awaiting": ("#F59E0B", "rgba(245,158,11,0.12)"),
        "pending": ("#3B82F6", "rgba(59,130,246,0.12)"),
        "printing": ("#3B82F6", "rgba(59,130,246,0.12)"),
        "queued": ("#94A3B8", "rgba(148,163,184,0.10)"),
        "failed": ("#EF4444", "rgba(239,68,68,0.12)"),
        "printed": ("#22C55E", "rgba(34,197,94,0.12)"),
        "done": ("#22C55E", "rgba(34,197,94,0.12)"),
        "completed": ("#22C55E", "rgba(34,197,94,0.12)"),
        "verified": ("#22C55E", "rgba(34,197,94,0.12)"),
    }
    fg, bg = colors.get(status.lower(), ("#94A3B8", "rgba(148,163,184,0.10)"))
    w = QLabel(f"●  {text}")
    w.setAlignment(Qt.AlignCenter)
    w.setStyleSheet(
        f"color:{fg};background:{bg};border-radius:9px;padding:3px 8px;"
        "font-size:11px;font-weight:600;"
    )
    return w


class StatCard(QFrame):
    def __init__(self, label, icon="◆", accent=False):
        super().__init__()
        self.setObjectName("StatCard")
        if not accent:
            self.setStyleSheet("QFrame#StatCard { border-left: 3px solid #3B6578; }")
        self._hidden = False
        self._value = "—"
        self._sub_value = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 10)
        lay.setSpacing(5)
        top = QHBoxLayout()
        icon_box = QLabel(icon)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setFixedSize(30, 30)
        icon_box.setStyleSheet(
            f"background:{'rgba(249,115,22,0.15)' if accent else 'rgba(148,163,184,0.10)'};"
            f"color:{'#F97316' if accent else '#A8C0CC'};border-radius:8px;font-weight:800;"
        )
        top.addWidget(icon_box)
        top.addStretch()
        self.sub = QLabel("")
        self.sub.setStyleSheet("color:#75D69A;font-size:10px;font-weight:600;")
        top.addWidget(self.sub)
        self.hide_btn = QPushButton("◉")
        self.hide_btn.setToolTip("Hide / show this value")
        self.hide_btn.setProperty("class", "icon")
        self.hide_btn.setFixedSize(30, 28)
        self.hide_btn.clicked.connect(self.toggle_hidden)
        top.addWidget(self.hide_btn)
        lay.addLayout(top)
        self.value_label = QLabel("—")
        self.value_label.setObjectName("StatCardValue")
        self.value_label.setStyleSheet("font-size:20px;font-weight:800;color:#EAF2F5;")
        lay.addWidget(self.value_label)
        self.label = QLabel(label)
        self.label.setObjectName("StatCardLabel")
        lay.addWidget(self.label)

    def set_value(self, value, sub=""):
        self._value = str(value)
        self._sub_value = str(sub)
        self._render_value()

    def toggle_hidden(self):
        self._hidden = not self._hidden
        self.hide_btn.setText("◉" if self._hidden else "◌")
        self.hide_btn.setToolTip("Show this value" if self._hidden else "Hide this value")
        self._render_value()

    def _render_value(self):
        if self._hidden:
            self.value_label.setText("***")
            self.sub.setText("***")
        else:
            self.value_label.setText(self._value)
            self.sub.setText(self._sub_value)


class ReceiptDetailDialog(QDialog):
    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Receipt #{int(row.get('receipt_no') or 0):06d}")
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(10)

        title = QLabel("Payment Receipt")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        receipt = QLabel(f"RECEIPT #{int(row.get('receipt_no') or 0):06d}")
        receipt.setStyleSheet("color:#F97316;font-size:18px;font-weight:800;")
        lay.addWidget(receipt)

        info = QFrame()
        info.setObjectName("SectionCard")
        form = QGridLayout(info)
        form.setContentsMargins(16, 16, 16, 16)
        data = [
            ("Date / Time", local_time_text(row.get("verified_at"))),
            ("Payment Method", str(row.get("payment_method") or "—")),
            ("Amount", money(row.get("amount"))),
            ("Order ID", str(row.get("order_id") or "—")),
            ("Computer", str(row.get("computer_name") or "—")),
            ("Printer", str(row.get("printer_name") or "—")),
            ("Printing Type", str(row.get("printing_type") or "—")),
            ("Files", str(row.get("file_name") or "—")),
            ("Print Status", str(row.get("print_status") or "—")),
        ]
        for r, (k, v) in enumerate(data):
            kl = QLabel(k)
            kl.setStyleSheet("color:#64748B;font-size:11px;font-weight:600;")
            vl = QLabel(v)
            vl.setWordWrap(True)
            vl.setStyleSheet("color:#E2E8F0;font-size:12px;font-weight:600;")
            form.addWidget(kl, r, 0)
            form.addWidget(vl, r, 1)
        lay.addWidget(info, 1)

        close = QPushButton("Close")
        close.setProperty("class", "primary")
        close.clicked.connect(self.accept)
        lay.addWidget(close)


class ReceiptHistoryDialog(QDialog):
    """Persistent receipt history sourced directly from verified payments."""
    def __init__(self, period="today", method="", parent=None):
        super().__init__(parent)
        self.period = period
        self.method = method
        self.rows = []
        self.setWindowTitle("Receipt History")
        self.resize(1360, 700)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("Receipt History")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        head.addStretch()
        self.summary = QLabel("")
        self.summary.setObjectName("Hint")
        head.addWidget(self.summary)
        close = QPushButton("Close")
        close.setProperty("class", "ghost")
        close.clicked.connect(self.accept)
        head.addWidget(close)
        lay.addLayout(head)

        controls = QHBoxLayout()
        self.period_buttons = []
        for val in ("today", "week", "month", "all"):
            b = QPushButton(PERIOD_LABELS[val])
            b.setProperty("class", "primary" if val == period else "ghost")
            b.clicked.connect(lambda _, v=val: self.change_period(v))
            controls.addWidget(b)
            self.period_buttons.append((val, b))
        method_box = QComboBox()
        method_box.addItem("All Methods", "ALL")
        method_box.addItem("Cash", "CASH")
        method_box.addItem("Online", "ONLINE")
        method_box.setCurrentIndex(next((i for i in range(method_box.count()) if method_box.itemData(i) == method), 0))
        method_box.currentIndexChanged.connect(lambda _: self.change_method(method_box.currentData()))
        controls.addWidget(method_box)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search receipt, order, printer or computer…")
        self.search.textChanged.connect(self._apply_search)
        controls.addWidget(self.search, 1)
        csv_btn = QPushButton("⇩  Export CSV")
        csv_btn.setProperty("class", "secondary")
        csv_btn.setToolTip("Save the receipts currently shown (period, payment method and search) as a CSV file")
        csv_btn.clicked.connect(self.export_csv)
        controls.addWidget(csv_btn)
        lay.addLayout(controls)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Receipt #", "Date / Time", "Method", "Amount", "Order ID",
            "Files", "Printing Type", "Printer", "Computer", "Status"
        ])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Fixed)
        column_widths = [85, 170, 90, 105, 120, 190, 130, 170, 110, 105]
        for i, width in enumerate(column_widths):
            hh.resizeSection(i, width)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.view_receipt)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        hint = QLabel("Double-click a receipt to view the complete receipt.")
        hint.setObjectName("Hint")
        foot.addWidget(hint)
        foot.addStretch()
        self.view_btn = QPushButton("View Selected Receipt")
        self.view_btn.setProperty("class", "secondary")
        self.view_btn.clicked.connect(self.view_selected)
        foot.addWidget(self.view_btn)
        lay.addLayout(foot)
        self.refresh()

    def change_period(self, value):
        self.period = value
        for val, button in self.period_buttons:
            button.setProperty("class", "primary" if val == value else "ghost")
            button.style().unpolish(button)
            button.style().polish(button)
        self.refresh()

    def change_method(self, value):
        self.method = value or ""
        self.refresh()

    def refresh(self):
        try:
            self.rows = api_client.get_receipts(self.period, self.method) or []
        except ApiClientError as e:
            self.rows = []
            self.table.setRowCount(0)
            self.summary.setText("⚠ " + e.message)
            return
        self._apply_search(self.search.text())

    def _filtered_rows(self):
        """Rows matching the search box. Used by the table, the detail view and the CSV export."""
        q = (self.search.text() or "").strip().lower()
        rows = []
        for r in self.rows:
            hay = " ".join(str(r.get(k) or "") for k in (
                "receipt_no", "order_id", "payment_method", "printer_name",
                "computer_name", "file_name", "printing_type"
            )).lower()
            if not q or q in hay:
                rows.append(r)
        return rows

    def _apply_search(self, text=None):
        rows = self._filtered_rows()
        self.table.setRowCount(len(rows))
        total = sum(float(r.get("amount") or 0) for r in rows)
        label = PERIOD_LABELS.get(self.period, self.period)
        if rows:
            self.summary.setText(f"{label}  ·  {len(rows)} receipts  ·  {money(total)}")
        else:
            self.summary.setText(f"{label}  ·  No verified receipts found")
        for i, r in enumerate(rows):
            vals = [
                f"#{int(r.get('receipt_no') or 0):06d}",
                local_time_text(r.get("verified_at")),
                str(r.get("payment_method") or "—"),
                money(r.get("amount")),
                str(r.get("order_id") or "")[:12],
                str(r.get("file_name") or "—"),
                str(r.get("printing_type") or "—"),
                str(r.get("printer_name") or "—"),
                str(r.get("computer_name") or "—"),
                str(r.get("print_status") or "—"),
            ]
            for c, v in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem(v))

    def export_csv(self):
        """Save exactly what is selected on screen (period + payment method + search) as CSV."""
        rows = self._filtered_rows()
        if not rows:
            QMessageBox.information(
                self, "Nothing to export",
                "There are no receipts in the current selection.\n\n"
                "Choose another period or payment method, or clear the search box."
            )
            return

        default_name = f"SialviPrint_Receipts_{self.period}_{datetime.now().strftime('%Y-%m-%d')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Receipts CSV", default_name, "CSV Files (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        headers = [
            "S.No", "Receipt #", "Date / Time", "Payment Method", "Amount (PKR)",
            "Order ID", "Files", "Pages", "Quantity", "Printing Type",
            "Printer", "Computer", "Print Status",
        ]
        try:
            # utf-8-sig adds the BOM so Excel opens the file with correct characters.
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(headers)
                total = 0.0
                for idx, r in enumerate(rows, 1):
                    amount = float(r.get("amount") or 0)
                    total += amount
                    w.writerow([
                        idx,
                        f"#{int(r.get('receipt_no') or 0):06d}",
                        local_time_text(r.get("verified_at")),
                        csv_safe(r.get("payment_method") or ""),
                        f"{amount:.2f}",
                        csv_safe(r.get("order_id") or ""),
                        csv_safe(r.get("file_name") or ""),
                        int(r.get("total_pages") or 0),
                        int(r.get("total_quantity") or r.get("total_pages") or 0),
                        csv_safe(r.get("printing_type") or ""),
                        csv_safe(r.get("printer_name") or ""),
                        csv_safe(r.get("computer_name") or ""),
                        csv_safe(r.get("print_status") or ""),
                    ])
                w.writerow(["", "TOTAL", "", "", f"{total:.2f}", "", "", "", "", "", "", "", ""])
        except OSError as e:
            QMessageBox.critical(
                self, "CSV export failed",
                f"Could not save the file. If it is open in Excel, close it and try again.\n\n{e}"
            )
            return
        QMessageBox.information(
            self, "CSV saved",
            f"{len(rows)} receipts exported ({PERIOD_LABELS.get(self.period, self.period)}).\n\n{path}"
        )

    def view_selected(self):
        row = self.table.currentRow()
        if row >= 0:
            self.view_receipt(row, 0)

    def view_receipt(self, row, _column):
        visible = self._filtered_rows()
        if 0 <= row < len(visible):
            ReceiptDetailDialog(visible[row], self).exec()


class DashboardScreen(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title_row = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.date_label = QLabel("")
        self.date_label.setObjectName("Hint")
        self.date_label.setMinimumWidth(235)
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        title_row.addWidget(self.date_label)
        layout.addLayout(title_row)

        self.error_banner = QLabel("")
        self.error_banner.setObjectName("StatusBannerError")
        self.error_banner.setWordWrap(True)
        self.error_banner.setVisible(False)
        layout.addWidget(self.error_banner)

        # Six requested dashboard columns.
        grid = QGridLayout()
        grid.setSpacing(10)
        self.today = StatCard("Daily Earnings", "↗", True)
        self.week = StatCard("Weekly Earnings", "↗")
        self.orders = StatCard("Today Orders", "▤")
        self.online = StatCard("Online Earnings", "◎")
        self.cash = StatCard("Cash Earnings", "₨")
        self.printers = StatCard("Printers Available", "⎙")
        for i, card in enumerate((self.today, self.week, self.orders, self.online, self.cash, self.printers)):
            grid.addWidget(card, 0, i)
        layout.addLayout(grid)

        lower = QHBoxLayout()
        lower.setSpacing(14)

        recent_card = QFrame()
        recent_card.setObjectName("SectionCard")
        rv = QVBoxLayout(recent_card)
        rv.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.setContentsMargins(18, 14, 18, 10)
        htitle = QLabel("Recent Orders")
        htitle.setObjectName("CardTitle")
        head.addWidget(htitle)
        head.addStretch()
        view_btn = QPushButton("View receipts  ›")
        view_btn.setProperty("class", "link")
        view_btn.clicked.connect(lambda: self.open_receipts("today"))
        head.addWidget(view_btn)
        rv.addLayout(head)
        self.recent_rows = QVBoxLayout()
        self.recent_rows.setContentsMargins(18, 4, 18, 12)
        self.recent_rows.setSpacing(0)
        rv.addLayout(self.recent_rows)
        lower.addWidget(recent_card, 2)

        queue_card = QFrame()
        queue_card.setObjectName("SectionCard")
        qv = QVBoxLayout(queue_card)
        qv.setContentsMargins(0, 0, 0, 0)
        qhead = QHBoxLayout()
        qhead.setContentsMargins(18, 14, 18, 10)
        qt = QLabel("Live Queue")
        qt.setObjectName("CardTitle")
        qhead.addWidget(qt)
        qhead.addStretch()
        qbtn = QPushButton("Refresh  ↻")
        qbtn.setProperty("class", "link")
        qbtn.clicked.connect(self.refresh)
        qhead.addWidget(qbtn)
        qv.addLayout(qhead)
        self.queue_rows = QVBoxLayout()
        self.queue_rows.setContentsMargins(14, 4, 14, 14)
        self.queue_rows.setSpacing(8)
        qv.addLayout(self.queue_rows)
        lower.addWidget(queue_card, 1)
        layout.addLayout(lower, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self.refresh()

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if widget:
                widget.deleteLater()
            elif child:
                self._clear_layout(child)

    def _order_row(self, r):
        row = QWidget()
        row.setMinimumHeight(58)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 6, 0, 6)
        icon = QLabel("↯")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(32, 32)
        icon.setStyleSheet("background:#454040;color:#64748B;border-radius:9px;font-size:12px;")
        rl.addWidget(icon)
        mid = QVBoxLayout()
        mid.setSpacing(1)
        oid = str(r.get("order_id") or "")
        station = r.get("station_id") or r.get("computer_name") or "Station"
        mid.addWidget(QLabel(f"ORD-{oid[:8].upper()}  ·  {station}"))
        mid.itemAt(0).widget().setStyleSheet("font-family:Consolas;font-size:11px;color:#94A3B8;")
        detail = QLabel(
            f"{r.get('printing_type') or 'Printing'} · {r.get('total_pages') or 0} pages · "
            f"{r.get('total_price') and money(r.get('total_price')) or money(0)}"
        )
        detail.setStyleSheet("font-size:12px;color:#64748B;")
        mid.addWidget(detail)
        rl.addLayout(mid, 1)
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignRight)
        amount = QLabel(money(r.get("total_price")))
        amount.setStyleSheet("font-size:13px;font-weight:700;color:#E2E8F0;")
        right.addWidget(amount, alignment=Qt.AlignRight)
        status = str(r.get("badge_status") or "awaiting").lower()
        label = {"awaiting":"Awaiting", "pending":"Pending", "done":"Done", "failed":"Failed"}.get(status, status.title())
        right.addWidget(badge(label, status), alignment=Qt.AlignRight)
        rl.addLayout(right)
        return row

    def _queue_row(self, job):
        card = QFrame()
        card.setStyleSheet("QFrame { background:#2C2929; border-radius:8px; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(11, 9, 11, 9)
        top = QHBoxLayout()
        jid = QLabel(str(job.get("id", ""))[:14])
        jid.setStyleSheet("font-family:Consolas;font-size:11px;color:#64748B;")
        top.addWidget(jid)
        top.addStretch()
        top.addWidget(badge(str(job.get("status", "")).lower(), str(job.get("status", ""))))
        v.addLayout(top)
        printer = QLabel(job.get("printer_name") or "Printer")
        printer.setStyleSheet("font-size:11px;color:#64748B;")
        v.addWidget(printer)
        return card

    def open_receipts(self, period="today", method=""):
        ReceiptHistoryDialog(period, method, self).exec()

    def refresh(self):
        self.date_label.setText(datetime.now().strftime("%A, %d %B %Y"))
        try:
            d = api_client.get_dashboard()
            jobs = api_client.get_queue() or []
        except ApiClientError as e:
            self.error_banner.setText("⚠ " + e.message)
            self.error_banner.setVisible(True)
            return

        self.error_banner.setVisible(False)
        self.today.set_value(money(d.get("today_earnings")), pct_text(d.get("today_change_pct")))
        self.week.set_value(money(d.get("week_earnings")), pct_text(d.get("week_change_pct")))
        self.orders.set_value(str(d.get("today_orders", 0)), pct_text(d.get("today_orders_change_pct")))
        self.online.set_value(money(d.get("today_online")), "Today")
        self.cash.set_value(money(d.get("today_cash")), "Today")
        self.printers.set_value(f"{d.get('printers_available', 0)}", "Available")

        self._clear_layout(self.recent_rows)
        for r in (d.get("recent_orders") or [])[:7]:
            self.recent_rows.addWidget(self._order_row(r))
        if not (d.get("recent_orders") or []):
            empty = QLabel("No orders yet today")
            empty.setObjectName("Hint")
            empty.setAlignment(Qt.AlignCenter)
            self.recent_rows.addWidget(empty)

        self._clear_layout(self.queue_rows)
        for job in jobs[:5]:
            self.queue_rows.addWidget(self._queue_row(job))
        if not jobs:
            empty = QLabel("Queue is clear")
            empty.setObjectName("Hint")
            empty.setAlignment(Qt.AlignCenter)
            self.queue_rows.addWidget(empty)
