"""Small dialogs for the Owner License Manager."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from owner.ui.style import STATUS_COLORS
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView, QAbstractItemView)


def fill_table(table: QTableWidget, headers, rows, keys=None):
    """keys[i] is stored invisibly on row i (Qt.UserRole of column 0), so columns can be ordered freely."""
    table.setSortingEnabled(False)
    table.clear()
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(36)
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QTableWidgetItem("" if val is None else str(val))
            if c == 0 and keys is not None:
                item.setData(Qt.UserRole, keys[r])
            item.setToolTip("" if val is None else str(val))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            colour = STATUS_COLORS.get(str(val).strip().lower()) if headers[c].lower() in ("status", "state") else None
            if colour:                  # status words are coloured AND always written out (never colour-only)
                item.setForeground(QColor(colour)); f = item.font(); f.setBold(True); item.setFont(f)
            table.setItem(r, c, item)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)
    table.setSortingEnabled(True)


def ts(value):
    """2026-10-18T14:16:27Z -> 2026-10-18 14:16  (column headers say UTC)."""
    return value.replace("T", " ").replace("Z", "")[:16] if value else "—"


class CreateLicenseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New License")
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        self.label = QLineEdit(); self.label.setPlaceholderText("e.g. Printing Shop A")
        self.limit = QSpinBox(); self.limit.setRange(1, 100000); self.limit.setValue(5)
        self.limit.setButtonSymbols(QSpinBox.NoButtons); self.limit.setToolTip("Type a number (1 - 100000)")
        self.status = QComboBox(); self.status.addItems(["active", "suspended"])
        self.use_exp = QCheckBox("Expires on (UTC date, YYYY-MM-DD):")
        self.exp = QLineEdit(); self.exp.setEnabled(False); self.exp.setPlaceholderText("2027-12-31")
        self.use_exp.toggled.connect(self.exp.setEnabled)
        self.notes = QPlainTextEdit(); self.notes.setFixedHeight(80); self.notes.setPlaceholderText("Optional notes (only you see these)")
        self.error = QLabel(""); self.error.setObjectName("Error"); self.error.setWordWrap(True)
        form.addRow("Customer / license name *", self.label)
        form.addRow("Maximum PCs *", self.limit)
        form.addRow("Initial status", self.status)
        form.addRow(self.use_exp, self.exp)
        form.addRow("Notes", self.notes)
        form.addRow(self.error)
        row = QHBoxLayout()
        ok = QPushButton("Create License"); ok.setProperty("class", "primary"); ok.clicked.connect(self._ok)
        cancel = QPushButton("Cancel"); cancel.setProperty("class", "ghost"); cancel.clicked.connect(self.reject)
        row.addStretch(); row.addWidget(cancel); row.addWidget(ok)
        form.addRow(row)

    def _ok(self):
        if not self.label.text().strip():
            self.error.setText("⚠ Please type the customer / license name.")
            self.label.setFocus()
            return
        if self.use_exp.isChecked() and not self.exp.text().strip():
            self.error.setText("⚠ Type the expiration date (YYYY-MM-DD) or untick the box.")
            return
        self.accept()

    def values(self):
        return {"label": self.label.text().strip(), "max_installations": self.limit.value(), "status": self.status.currentText(),
                "notes": self.notes.toPlainText().strip(),
                "expires_at": self.exp.text().strip() if self.use_exp.isChecked() else None}


class KeyDialog(QDialog):
    """Shows the new serial key ONCE (only its hash is stored on the server)."""
    def __init__(self, label, key, usage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("License created")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{label}</b> — {usage}"))
        self.key = QLineEdit(key); self.key.setReadOnly(True)
        self.key.setStyleSheet("font-size:22px; font-weight:700; font-family:Consolas,'Courier New',monospace; padding:12px;"
                               " border:2px solid #F97316; color:#FFFFFF; background:#0B0D13;")
        lay.addWidget(self.key)
        warn = QLabel("Copy this key now and send it to the customer. For security only a hash is stored on the "
                      "server, so it cannot be shown again.")
        warn.setWordWrap(True)
        lay.addWidget(warn)
        self.copy = QPushButton("Copy Key"); self.copy.setProperty("class", "primary"); self.copy.clicked.connect(self._copy)
        done = QPushButton("Close"); done.setProperty("class", "ghost"); done.clicked.connect(self.accept)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(done); row.addWidget(self.copy)
        lay.addLayout(row)

    def _copy(self):
        QGuiApplication.clipboard().setText(self.key.text())
        self.copy.setText("Copied ✓")


class LimitDialog(QDialog):
    def __init__(self, label, current, usage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit PC Limit")
        form = QFormLayout(self)
        form.addRow(QLabel(f"<b>{label}</b><br>Currently {usage} registered, limit {current}."))
        self.limit = QSpinBox(); self.limit.setRange(1, 100000); self.limit.setValue(current); self.limit.setButtonSymbols(QSpinBox.NoButtons)
        self.reason = QLineEdit()
        form.addRow("New maximum PCs", self.limit)
        form.addRow("Reason (optional)", self.reason)
        row = QHBoxLayout()
        ok = QPushButton("Save"); ok.setProperty("class", "primary"); ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel"); cancel.setProperty("class", "ghost"); cancel.clicked.connect(self.reject)
        row.addStretch(); row.addWidget(cancel); row.addWidget(ok)
        form.addRow(row)


class ConflictDialog(QDialog):
    """Lowering the limit below current usage: nothing is deleted unless the owner explicitly picks PCs."""
    def __init__(self, error, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PC limit conflict")
        self.setMinimumWidth(520)
        self.need = error["must_free"]
        lay = QVBoxLayout(self)
        msg = QLabel(f"{error['usage']} PCs are registered but the new limit is {error['new_limit']}.<br>"
                     f"Nothing has been changed. To continue, <b>choose at least {self.need} PC(s) to deactivate</b> "
                     "(their history is kept), or cancel and keep a higher limit.")
        msg.setWordWrap(True); lay.addWidget(msg)
        self.list = QListWidget()
        for i in error["installations"]:
            it = QListWidgetItem(f"{i['device_name'] or i['id']}  —  last IP {i.get('last_ip') or '—'}  —  {i['status']}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(Qt.Unchecked); it.setData(Qt.UserRole, i["id"])
            self.list.addItem(it)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        self.ok = QPushButton("Deactivate selected and apply limit"); self.ok.setProperty("class", "danger"); self.ok.clicked.connect(self._ok)
        cancel = QPushButton("Cancel"); cancel.setProperty("class", "ghost"); cancel.clicked.connect(self.reject)
        row.addStretch(); row.addWidget(cancel); row.addWidget(self.ok); lay.addLayout(row)

    def _ok(self):
        if len(self.selected()) >= self.need:
            self.accept()

    def selected(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]


class RestrictionDialog(QDialog):
    SCOPES = [("block_activation", "Block NEW installations from activating from this IP"),
              ("block_authorization", "Block ALL authorization (activation + renewal) from this IP")]

    def __init__(self, licenses, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add IP Restriction")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        self.net = QLineEdit(); self.net.setPlaceholderText("203.0.113.10  or  203.0.113.0/24  or  2001:db8::/48")
        self.scope = QComboBox()
        for v, text in self.SCOPES:
            self.scope.addItem(text, v)
        self.lic = QComboBox(); self.lic.addItem("ALL licenses (global)", None)
        for l in licenses:
            self.lic.addItem(f"{l['display_id']}  {l['label']}", l["id"])
        self.reason = QLineEdit()
        note = QLabel("A public IP is not a person: many PCs/customers can share one address (office, ISP, carrier NAT).")
        note.setWordWrap(True)
        form.addRow("IP address / range *", self.net); form.addRow("What to block *", self.scope)
        form.addRow("Applies to *", self.lic); form.addRow("Reason", self.reason); form.addRow(note)
        row = QHBoxLayout()
        ok = QPushButton("Add Restriction"); ok.setProperty("class", "primary"); ok.clicked.connect(self._ok)
        cancel = QPushButton("Cancel"); cancel.setProperty("class", "ghost"); cancel.clicked.connect(self.reject)
        row.addStretch(); row.addWidget(cancel); row.addWidget(ok); form.addRow(row)

    def _ok(self):
        if self.net.text().strip():
            self.accept()

    def values(self):
        return {"network": self.net.text().strip(), "scope": self.scope.currentData(),
                "license_id": self.lic.currentData(), "reason": self.reason.text().strip()}


class TableDialog(QDialog):
    def __init__(self, title, headers, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 420)
        lay = QVBoxLayout(self)
        t = QTableWidget(); fill_table(t, headers, rows); lay.addWidget(t)
        close = QPushButton("Close"); close.setProperty("class", "ghost"); close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)
