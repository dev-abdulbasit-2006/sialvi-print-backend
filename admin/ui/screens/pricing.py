"""
Pricing screen: the Admin's control over every print type and its price
(requirement 3.2). This is the ONLY place prices can be changed - User
apps only ever read from /api/profiles.

Admin can:
  * change the base price per page of any print type
  * add a NEW print type (e.g. "Card Printing") with its price
  * define PACKAGES (quantity tiers), e.g. 1-3 pages = Rs 40/page,
    4-10 pages = Rs 30/page ...
  * choose which print types ask the customer for print properties
    (paper size / quality / paper type) before paying
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QHeaderView, QDoubleSpinBox, QCheckBox, QDialog,
    QFormLayout, QLineEdit, QComboBox, QSpinBox, QDialogButtonBox, QScrollArea,
    QFrame, QGridLayout,
)

from admin.ui.api_client import api_client, ApiClientError

logger = logging.getLogger("admin_ui.pricing")


# ------------------------------------------------------------------
# Dialog: add a new print type
# ------------------------------------------------------------------
class AddPrintTypeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Print Type")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = QLabel("Add a new print type")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        hint = QLabel("Create the printing options used by your shop. There are no fixed built-in printing types in this screen. "
                      "straight away with the price you set here.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Card Printing")
        form.addRow("Name:", self.name_edit)

        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0, 100000)
        self.price_spin.setDecimals(2)
        self.price_spin.setSingleStep(1)
        self.price_spin.setPrefix("Rs ")
        form.addRow("Price per page:", self.price_spin)

        self.color_combo = QComboBox()
        self.color_combo.addItem("Color", "COLOR")
        self.color_combo.addItem("Black & White", "BW")
        form.addRow("Printing:", self.color_combo)

        self.paper_combo = QComboBox()
        self.paper_combo.setEditable(True)
        self.paper_combo.addItems(["A4", "A3", "A5", "Letter"])
        form.addRow("Paper size:", self.paper_combo)
        lay.addLayout(form)

        self.ask_check = QCheckBox("Enable print-properties popup for customers")
        self.ask_check.setChecked(False)
        lay.addWidget(self.ask_check)
        self.enabled_check = QCheckBox("Printing enabled for customers")
        self.enabled_check.setChecked(True)
        lay.addWidget(self.enabled_check)
        tip = QLabel("You can add quantity packages (like 1–3 pages = Rs 40) after creating it, "
                     "using the “Packages” button.")
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Add Print Type")
        buttons.button(QDialogButtonBox.Ok).setProperty("class", "primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Name needed", "Please enter a name for the print type.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "display_name": self.name_edit.text().strip(),
            "price_per_page": self.price_spin.value(),
            "color_mode": self.color_combo.currentData(),
            "paper_size": self.paper_combo.currentText().strip() or "A4",
            "ask_properties": self.ask_check.isChecked(),
            "enabled": self.enabled_check.isChecked(),
        }


# ------------------------------------------------------------------
# Dialog: packages (quantity tiers)
# ------------------------------------------------------------------
class PackagesDialog(QDialog):
    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Packages — {profile['display_name']}")
        self.setMinimumSize(640, 470)
        self.profile = profile

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        title = QLabel(f"Packages for {profile['display_name']}")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        hint = QLabel(
            "Set a price per page for different quantities. Example: 1–3 pages = Rs 40, "
            "4–10 pages = Rs 30, 11 and above = Rs 25.\n"
            "The package matching the order's total pages (pages × copies) sets the price per page "
            "for the whole order. Leave “To” as “and above” for the last package. "
            "With no packages, the normal price per page is used."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["From (pages)", "To (pages)", "Price per page (Rs)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table, 1)

        row_btns = QHBoxLayout()
        add_btn = QPushButton("＋  Add Package")
        add_btn.setProperty("class", "secondary")
        add_btn.clicked.connect(self._add_next_row)
        rm_btn = QPushButton("Remove Selected")
        rm_btn.setProperty("class", "ghost")
        rm_btn.clicked.connect(self._remove_selected)
        row_btns.addWidget(add_btn)
        row_btns.addWidget(rm_btn)
        row_btns.addStretch()
        lay.addLayout(row_btns)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.button(QDialogButtonBox.Save).setProperty("class", "primary")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        for t in profile.get("tiers", []):
            self._add_row(t["min_qty"], t["max_qty"], t["price_per_page"])

    def _add_row(self, lo=1, hi=None, price=0.0):
        r = self.table.rowCount()
        self.table.insertRow(r)
        lo_spin = QSpinBox()
        lo_spin.setRange(1, 1_000_000)
        lo_spin.setValue(int(lo))
        hi_spin = QSpinBox()
        hi_spin.setRange(0, 1_000_000)
        hi_spin.setSpecialValueText("and above")
        hi_spin.setValue(0 if hi is None else int(hi))
        price_spin = QDoubleSpinBox()
        price_spin.setRange(0, 100000)
        price_spin.setDecimals(2)
        price_spin.setSingleStep(0.5)
        price_spin.setValue(float(price))
        for col, w in enumerate((lo_spin, hi_spin, price_spin)):
            self.table.setCellWidget(r, col, w)

    def _add_next_row(self):
        last_hi = None
        if self.table.rowCount():
            v = self.table.cellWidget(self.table.rowCount() - 1, 1).value()
            last_hi = v if v > 0 else None
        if self.table.rowCount() and last_hi is None:
            QMessageBox.information(self, "Last package is open-ended",
                                    "The last package is set to “and above”. Give it a “To” value first "
                                    "if you want to add another package after it.")
            return
        start = (last_hi + 1) if last_hi else 1
        self._add_row(start, None, 0.0)

    def _remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.rowCount():
            rows = [self.table.rowCount() - 1]
        for r in rows:
            self.table.removeRow(r)

    def _save(self):
        tiers = []
        for r in range(self.table.rowCount()):
            hi = self.table.cellWidget(r, 1).value()
            tiers.append({
                "min_qty": self.table.cellWidget(r, 0).value(),
                "max_qty": hi if hi > 0 else None,
                "price_per_page": self.table.cellWidget(r, 2).value(),
            })
        try:
            api_client.set_tiers(self.profile["id"], tiers)
        except ApiClientError as e:
            QMessageBox.warning(self, "Packages not saved", e.message)
            return
        self.accept()


# ------------------------------------------------------------------
# Screen
# ------------------------------------------------------------------
class PricingScreen(QWidget):
    """Figma-style profile cards while preserving every real pricing action."""
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("Print Profiles & Pricing")
        title.setObjectName("PageTitle")
        title_col.addWidget(title)
        sub = QLabel("Adjust per-page rates, packages and customer print-property options")
        sub.setObjectName("Hint")
        title_col.addWidget(sub)
        header.addLayout(title_col)
        header.addStretch()
        add_btn = QPushButton("＋  Add Print Type")
        add_btn.setProperty("class", "secondary")
        add_btn.clicked.connect(self.on_add_type)
        header.addWidget(add_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("class", "primary")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        self.error_banner = QLabel("")
        self.error_banner.setObjectName("StatusBannerError")
        self.error_banner.setWordWrap(True)
        self.error_banner.setVisible(False)
        layout.addWidget(self.error_banner)

        scroll = QScrollArea()
        scroll.setObjectName("PricingScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background:#171717;border:none;")
        container = QWidget()
        container.setObjectName("PricingScrollContent")
        container.setStyleSheet("background:#171717;")
        self.grid = QGridLayout(container)
        self.grid.setContentsMargins(0, 0, 6, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        self._profiles = []
        self.refresh()

    def clear_cards(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _profile_card(self, p):
        card = QFrame()
        card.setObjectName("SectionCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        top = QHBoxLayout()
        icon = QLabel("▣" if p.get("color_mode") == "COLOR" else "▤")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(38, 38)
        icon.setStyleSheet(
            "background:rgba(59,130,246,.12);color:#3B82F6;border-radius:9px;font-size:16px;"
            if p.get("color_mode") == "COLOR"
            else "background:rgba(148,163,184,.08);color:#64748B;border-radius:9px;font-size:16px;"
        )
        top.addWidget(icon)
        info = QVBoxLayout()
        name = QLabel(p.get("display_name", "Print Profile"))
        name.setStyleSheet("font-size:13px;font-weight:700;color:#E2E8F0;")
        info.addWidget(name)
        tags = QLabel(f"{p.get('paper_size','—')}  ·  {p.get('media_type','—')}  ·  {p.get('color_mode','—')}")
        tags.setStyleSheet("color:#64748B;font-size:10px;")
        info.addWidget(tags)
        top.addLayout(info, 1)
        lay.addLayout(top)

        price_row = QHBoxLayout()
        pk = QLabel("PKR")
        pk.setStyleSheet("color:#64748B;font-size:11px;")
        price_row.addWidget(pk)
        spin = QDoubleSpinBox()
        spin.setRange(0, 100000)
        spin.setDecimals(2)
        spin.setSingleStep(1)
        spin.setValue(float(p.get("price_per_page", 0)))
        spin.setFixedWidth(105)
        price_row.addWidget(spin)
        per = QLabel("/ page")
        per.setStyleSheet("color:#64748B;font-size:11px;")
        price_row.addWidget(per)
        save = QPushButton("Save")
        save.setProperty("class", "primary")
        save.clicked.connect(lambda _, pid=p["id"], s=spin: self.on_price_changed(pid, s.value()))
        price_row.addWidget(save)
        price_row.addStretch()
        lay.addLayout(price_row)

        actions = QHBoxLayout()
        packages = QPushButton(f"Packages ({len(p.get('tiers', []))})" if p.get("tiers") else "Set Packages")
        packages.setProperty("class", "secondary" if p.get("tiers") else "ghost")
        packages.clicked.connect(lambda _, prof=p: self.on_packages(prof))
        actions.addWidget(packages)

        ask = QCheckBox("Print properties")
        ask.setChecked(bool(p.get("ask_properties")))
        ask.stateChanged.connect(lambda state, pid=p["id"]: self.on_ask_changed(pid, state))
        actions.addWidget(ask)

        enabled = QCheckBox("Enabled")
        enabled.setChecked(bool(p.get("is_enabled")))
        enabled.stateChanged.connect(lambda state, pid=p["id"]: self.on_enabled_changed(pid, state))
        actions.addWidget(enabled)
        actions.addStretch()
        lay.addLayout(actions)

        if str(p.get("code", "")).startswith("CUSTOM_"):
            delete = QPushButton("Delete")
            delete.setProperty("class", "danger")
            delete.clicked.connect(lambda _, prof=p: self.on_delete(prof))
            actions.addWidget(delete)
        return card

    def refresh(self):
        try:
            profiles = api_client.get_all_profiles() or []
        except ApiClientError as e:
            self.error_banner.setText("⚠ " + e.message)
            self.error_banner.setVisible(True)
            return
        self.error_banner.setVisible(False)
        self._profiles = profiles
        self.clear_cards()
        for i, p in enumerate(profiles):
            self.grid.addWidget(self._profile_card(p), i // 2, i % 2)
        self.grid.setRowStretch((len(profiles) + 1) // 2, 1)

    def on_add_type(self):
        dlg = AddPrintTypeDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            api_client.create_profile(**dlg.values())
        except ApiClientError as e:
            QMessageBox.warning(self, "Could not add print type", e.message)
            return
        self.refresh()

    def on_packages(self, profile: dict):
        if PackagesDialog(profile, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def on_delete(self, profile: dict):
        reply = QMessageBox.question(
            self, "Delete Print Type",
            f"Delete “{profile['display_name']}”?\n\nThis can't be undone.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            api_client.delete_profile(profile["id"])
        except ApiClientError as e:
            QMessageBox.warning(self, "Can't delete", e.message)
        self.refresh()

    def on_price_changed(self, profile_id: int, new_price: float):
        try:
            api_client.set_price(profile_id, new_price)
        except ApiClientError as e:
            QMessageBox.critical(self, "Price Update Failed", e.message)
            self.refresh()
        else:
            self.refresh()

    def on_enabled_changed(self, profile_id: int, state):
        enabled = state == 2
        try:
            api_client.set_profile_enabled(profile_id, enabled)
        except ApiClientError as e:
            QMessageBox.critical(self, "Update Failed", e.message)
            self.refresh()

    def on_ask_changed(self, profile_id: int, state):
        try:
            api_client.set_ask_properties(profile_id, state == 2)
        except ApiClientError as e:
            QMessageBox.critical(self, "Update Failed", e.message)
            self.refresh()
