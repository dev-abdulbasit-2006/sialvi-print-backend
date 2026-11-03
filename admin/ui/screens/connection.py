"""
Connection screen: shows the three values a User PC needs
(address, port, connection code) so staff never have to open config files.
"""

import socket

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)

from admin.config.config import config
from shared.netutil import local_ipv4_addresses as _shared_local_ips


def local_ipv4_addresses() -> list[str]:
    """This PC's IPv4 addresses (Ethernet and/or Wi-Fi; no internet needed)."""
    return _shared_local_ips() or ["(not connected to a network)"]


class ValueRow(QWidget):
    def __init__(self, caption: str, value: str, masked: bool = False):
        super().__init__()
        self._value = value
        self._masked = masked
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        cap = QLabel(caption)
        cap.setObjectName("StatCardLabel")
        lay.addWidget(cap)

        row = QHBoxLayout()
        self.value_label = QLabel()
        self.value_label.setObjectName("Code")
        self.value_label.setWordWrap(True)
        row.addWidget(self.value_label, 1)

        if masked:
            self.toggle_btn = QPushButton("Show")
            self.toggle_btn.setProperty("class", "ghost")
            self.toggle_btn.clicked.connect(self._toggle)
            row.addWidget(self.toggle_btn)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setProperty("class", "primary")
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.copy_btn)
        lay.addLayout(row)
        self._render()

    def _render(self):
        self.value_label.setText("•" * 24 if self._masked else self._value)

    def _toggle(self):
        self._masked = not self._masked
        self.toggle_btn.setText("Show" if self._masked else "Hide")
        self._render()

    def _copy(self):
        QGuiApplication.clipboard().setText(self._value)
        self.copy_btn.setText("Copied ✓")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("Copy"))


class ConnectionScreen(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)

        title = QLabel("Connect User PCs")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        hint = QLabel(
            "On each User PC, open SialviPrint and enter these three values on the "
            "“Connect to Admin PC” screen. Keep this Admin app running while shops are open."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(10)

        card = QFrame()
        card.setObjectName("InfoCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(24, 22, 24, 22)
        card_lay.setSpacing(18)

        ips = local_ipv4_addresses()
        card_lay.addWidget(ValueRow("ADMIN PC ADDRESS" + ("ES" if len(ips) > 1 else ""),
                                    "   or   ".join(ips)))
        card_lay.addWidget(ValueRow("PORT", str(config.server_port)))
        card_lay.addWidget(ValueRow("CONNECTION CODE", config.lan_shared_token, masked=True))
        layout.addWidget(card)

        tip = QLabel("Works with Ethernet only - no internet or Wi-Fi needed. If a User PC cannot "
                     "connect: make sure both PCs are on the same IP range (same first three numbers), "
                     "this app is running, and run Fix_LAN_Firewall.bat once on this PC "
                     "(offline Ethernet is often treated as a “Public” network by Windows).")
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        layout.addSpacing(8)
        layout.addWidget(tip)
        layout.addStretch()
