"""Figma-style printer management with real Windows printer discovery."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QMessageBox, QFrame
from admin.ui.api_client import api_client, ApiClientError


def status_pill(text, kind="online"):
    if kind == "network":
        fg, bg = "#3B82F6", "rgba(59,130,246,.12)"
    else:
        fg, bg = "#22C55E", "rgba(34,197,94,.12)"
    w = QLabel(f"●  {text}")
    w.setStyleSheet(f"color:{fg};background:{bg};border-radius:9px;padding:4px 8px;font-size:11px;font-weight:600;")
    return w


class PrintersScreen(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("Printer Management")
        title.setObjectName("PageTitle")
        title_col.addWidget(title)
        sub = QLabel("Map printers to print profiles and monitor discovered devices")
        sub.setObjectName("Hint")
        title_col.addWidget(sub)
        header.addLayout(title_col)
        header.addStretch()
        refresh = QPushButton("Refresh")
        refresh.setProperty("class", "primary")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.error_banner = QLabel("")
        self.error_banner.setObjectName("StatusBannerError")
        self.error_banner.setVisible(False)
        self.error_banner.setWordWrap(True)
        layout.addWidget(self.error_banner)

        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(10)
        layout.addLayout(self.list_layout, 1)
        self._profiles = []
        self._printers = []
        self.refresh()

    def clear(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def printer_card(self, printer):
        card = QFrame()
        card.setObjectName("SectionCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        top = QHBoxLayout()
        icon = QLabel("⎙")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(46, 46)
        icon.setStyleSheet("background:rgba(34,197,94,.12);color:#22C55E;border-radius:11px;font-size:21px;")
        top.addWidget(icon)

        info = QVBoxLayout()
        name = QLabel(printer.get("display_name") or printer.get("system_name") or "Printer")
        name.setStyleSheet("font-size:14px;font-weight:700;color:#E2E8F0;")
        info.addWidget(name)
        meta = QHBoxLayout()
        meta.addWidget(status_pill("Available"))
        meta.addWidget(status_pill("Network" if printer.get("is_network") else "Local", "network" if printer.get("is_network") else "online"))
        meta.addStretch()
        info.addLayout(meta)
        top.addLayout(info, 1)

        profile_box = QHBoxLayout()
        profile_label = QLabel("Profile:")
        profile_label.setStyleSheet("color:#64748B;font-size:12px;")
        profile_box.addWidget(profile_label)
        combo = QComboBox()
        combo.addItem("— Select profile —", None)
        assigned = []
        for p in self._profiles:
            combo.addItem(p.get("display_name", "Profile"), p.get("id"))
            if p.get("default_printer_id") == printer.get("id"):
                assigned.append(p)
        if assigned:
            combo.setCurrentIndex(next((i for i in range(combo.count()) if combo.itemData(i) == assigned[0]["id"]), 0))
        combo.setMinimumWidth(250)
        profile_box.addWidget(combo)
        assign = QPushButton("Assign")
        assign.setProperty("class", "primary")
        assign.clicked.connect(lambda _, pid=printer.get("id"), c=combo: self.on_assign(pid, c))
        profile_box.addWidget(assign)
        top.addLayout(profile_box)
        lay.addLayout(top)

        if assigned:
            mapped = QLabel(
                "↗   Mapped to   " + ", ".join(p.get("display_name", "") for p in assigned[:3])
                + (" …" if len(assigned) > 3 else "")
            )
            mapped.setStyleSheet("background:#2C2929;color:#64748B;border-radius:8px;padding:8px;font-size:11px;")
            lay.addWidget(mapped)
        return card

    def refresh(self):
        try:
            self._profiles = api_client.get_all_profiles() or []
            self._printers = api_client.get_printers() or []
        except ApiClientError as e:
            self.error_banner.setText("⚠ " + e.message)
            self.error_banner.setVisible(True)
            return
        self.error_banner.setVisible(False)
        self.clear()
        for printer in self._printers:
            self.list_layout.addWidget(self.printer_card(printer))
        if not self._printers:
            empty = QLabel("No enabled printers were discovered on the Admin PC.")
            empty.setObjectName("Hint")
            empty.setAlignment(Qt.AlignCenter)
            self.list_layout.addWidget(empty)
        self.list_layout.addStretch()

    def on_assign(self, printer_id, combo):
        profile_id = combo.currentData()
        if profile_id is None:
            QMessageBox.warning(self, "Select a Profile", "Please choose a print profile first.")
            return
        try:
            api_client.set_profile_mapping(printer_id, profile_id)
        except ApiClientError as e:
            QMessageBox.critical(self, "Mapping Failed", e.message)
            return
        QMessageBox.information(self, "Mapped", "Printer successfully mapped to the selected profile.")
        self.refresh()
