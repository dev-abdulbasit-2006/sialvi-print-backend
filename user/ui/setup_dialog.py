"""
First-run setup: the operator enters the Admin PC's LAN IP and the
shared token (shown on the Admin PC's Settings screen) once per User
PC. Saved to this station's persistent config afterward.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QSpinBox, QPushButton,
    QLabel, QMessageBox, QApplication,
)

from user.config.config import config
from shared.resources import asset_path
from user.network.api_client import UserApiClient, ApiClientError
from shared.network_discovery import discover_admins
from shared.netutil import diagnose_admin, scan_for_admin, local_ipv4_addresses


class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Admin PC")
        self.setMinimumWidth(420)

        self.setWindowIcon(QIcon(asset_path("emblem.png")))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(12)

        emblem = QLabel()
        pix = QPixmap(asset_path("emblem.png"))
        if not pix.isNull():
            emblem.setPixmap(pix.scaled(84, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        emblem.setAlignment(Qt.AlignCenter)
        layout.addWidget(emblem)
        heading = QLabel("Connect to Admin PC")
        heading.setObjectName("PageTitle")
        heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading)

        info = QLabel(
            "Connect the User PC to the same router/network as the Admin PC. "
            "You can discover the Admin automatically, or enter its IP/hostname manually. "
            "The connection code is shown in Admin > Connect User PCs."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.host_input = QLineEdit(config.get("admin_host", ""))
        self.host_input.setPlaceholderText("e.g. 192.168.1.10 or ADMIN-PC")
        form.addRow("Admin PC Address:", self.host_input)

        discover_btn = QPushButton("Find Admin on Same Router / LAN")
        discover_btn.setProperty("class", "ghost")
        discover_btn.clicked.connect(self.on_discover)
        form.addRow("", discover_btn)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(int(config.get("admin_port", 8765)))
        form.addRow("Port:", self.port_input)

        self.token_input = QLineEdit(config.get("lan_shared_token", ""))
        self.token_input.setEchoMode(QLineEdit.Password)
        form.addRow("Connection Code:", self.token_input)

        layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        test_btn = QPushButton("Test Connection")
        test_btn.setProperty("class", "ghost")
        test_btn.clicked.connect(self.on_test)
        layout.addWidget(test_btn)

        save_btn = QPushButton("Save & Continue")
        save_btn.setProperty("class", "secondary")
        save_btn.clicked.connect(self.on_save)
        layout.addWidget(save_btn)

    def on_discover(self):
        self.status_label.setText("Searching this local network for SialviPrint Admin…")
        QApplication.processEvents()
        try:
            results = discover_admins(timeout=2.5)
        except OSError as e:
            results = []
            self.status_label.setText(f"⚠ Broadcast discovery could not start: {e}")
        if not results:
            # Broadcasts are often blocked (firewall / switch). Fall back to
            # checking the Admin port directly on every local network.
            self.status_label.setText("Broadcast found nothing - scanning this LAN for the Admin port…")
            QApplication.processEvents()
            try:
                results = scan_for_admin(port=int(self.port_input.value()))
            except OSError:
                results = []
        if not results:
            ips = ", ".join(local_ipv4_addresses()) or "none"
            self.status_label.setText(
                "⚠ No Admin PC was found. This PC's address(es): " + ips + ". "
                "Check the Ethernet cable, that both PCs use the same IP range, that the Admin app is "
                "running, and run Fix_LAN_Firewall.bat on the Admin PC. "
                "You can also type the Admin PC's IP address manually."
            )
            return
        if len(results) == 1:
            item = results[0]
            self.host_input.setText(item["host"])
            self.port_input.setValue(item["port"])
            self.status_label.setText(f"✅ Found {item['name']} at {item['host']}:{item['port']}")
            return

        # Keep the UI dependency-free: choose the first response deterministically,
        # but show all discovered addresses so the operator can correct it manually.
        item = sorted(results, key=lambda x: (x["host"], x["port"]))[0]
        self.host_input.setText(item["host"])
        self.port_input.setValue(item["port"])
        addresses = ", ".join(f"{x['host']}:{x['port']}" for x in results)
        self.status_label.setText(f"✅ Found {len(results)} Admin PCs: {addresses}. Using {item['host']}.")

    def _apply_to_config(self):
        config.set("admin_host", self.host_input.text().strip())
        config.set("admin_port", self.port_input.value())
        config.set("lan_shared_token", self.token_input.text().strip())

    def on_test(self):
        if not self.host_input.text().strip() or not self.token_input.text().strip():
            self.status_label.setText("⚠ Please enter both the address and connection code.")
            return
        ok, message = diagnose_admin(self.host_input.text().strip(), self.port_input.value())
        if not ok:
            self.status_label.setText("⚠ " + message)
            return
        self._apply_to_config()
        client = UserApiClient()
        try:
            client.get_profiles()
        except ApiClientError as e:
            self.status_label.setText(f"⚠ {e.message}")
            return
        self.status_label.setText("✅ Connected successfully.")

    def on_save(self):
        if not self.host_input.text().strip() or not self.token_input.text().strip():
            QMessageBox.warning(self, "Missing Information",
                                 "Please enter both the Admin PC address and connection code.")
            return
        self._apply_to_config()
        self.accept()
